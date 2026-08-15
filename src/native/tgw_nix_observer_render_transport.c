#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dirent.h>
#include <endian.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/capability.h>
#include <linux/securebits.h>
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/mman.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef TGW_RENDER_WRAPPER_CONFIG
#define TGW_RENDER_WRAPPER_CONFIG "/etc/tgw/nix-observer-render-wrapper.conf"
#endif

#define CONFIG_PATH TGW_RENDER_WRAPPER_CONFIG
#define CONFIG_MAX 32768
#define PACKET_PREFIX 172
#define LAUNCH_TRAILER 372
#define ENVELOPE_TTL 300
#define MAX_PACKET (128UL * 1024UL * 1024UL + 4UL * 1024UL * 1024UL)
#define PROBE_JSON "{\"direct_probe\":\"ENETUNREACH\",\"ipv4_route_count\":0,\"ipv6_route_count\":0,\"links\":[\"lo\"],\"loopback_state\":\"down\",\"schema\":\"tgw-render-netns-negative-probe/v1\"}"

struct config {
  uid_t uid;
  gid_t gid;
  char python[4096], python_exe[4096], ip[4096], bootstrap[4096], helper[4096], signing_key[4096];
  char python_sha256[72], ip_sha256[72], bootstrap_sha256[72], helper_sha256[72];
  char wrapper_sha256[72], request_sha256[72], prerequisite_sha256[72], public_key_sha256[72];
  unsigned char packet_magic[8];
  uint32_t packet_version;
  size_t max_output_bytes;
};

struct launch_binding {
  char plan_commit[41], source_commit[41], source_tree[41];
  char request_sha256[72], composition_sha256[72], attempt_id[33], generation[193];
};

static void die(const char *message) {
  dprintf(STDERR_FILENO, "tgw-render-wrapper: %s\n", message);
  _exit(125);
}

static void copy_bounded(char *out, size_t size, const char *value, size_t length) {
  if (!length || length >= size) die("configuration value length invalid");
  memcpy(out, value, length);
  out[length] = 0;
}

static unsigned long parse_number(const char *value, size_t length, unsigned long maximum) {
  char bounded[32];
  char *end = NULL;
  if (!length || length >= sizeof(bounded)) die("configuration number length invalid");
  for (size_t index = 0; index < length; index++) if (value[index] < '0' || value[index] > '9') die("configuration number grammar invalid");
  memcpy(bounded, value, length);
  bounded[length] = 0;
  errno = 0;
  unsigned long result = strtoul(bounded, &end, 10);
  if (errno || !result || !end || *end || result > maximum) die("configuration number invalid");
  return result;
}

static int lower_hex(const char *value, size_t length) {
  for (size_t index = 0; index < length; index++) if (!((value[index] >= '0' && value[index] <= '9') || (value[index] >= 'a' && value[index] <= 'f'))) return 0;
  return 1;
}

static void copy_digest(char out[72], const char *value, size_t length) {
  if (length != 71 || memcmp(value, "sha256:", 7) || !lower_hex(value + 7, 64)) die("configuration digest invalid");
  memcpy(out, value, 71);
  out[71] = 0;
}

static void copy_path(char out[4096], const char *value, size_t length) {
  if (length < 2 || length >= 4096 || value[0] != '/' || value[length - 1] == '/') die("configuration path invalid");
  for (size_t index = 1; index < length; index++) {
    unsigned char byte = (unsigned char)value[index];
    if (!((byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z') || (byte >= '0' && byte <= '9') ||
          byte == '/' || byte == '.' || byte == '_' || byte == '-' || byte == '+')) die("configuration path grammar invalid");
  }
  size_t component = 1;
  for (size_t index = 1; index <= length; index++) {
    if (index == length || value[index] == '/') {
      size_t component_length = index - component;
      if (!component_length || (component_length == 1 && value[component] == '.') ||
          (component_length == 2 && value[component] == '.' && value[component + 1] == '.')) die("configuration path component invalid");
      component = index + 1;
    }
  }
  copy_bounded(out, 4096, value, length);
}

static void digest_from_raw(const unsigned char raw[32], char out[72]) {
  static const char digits[] = "0123456789abcdef";
  memcpy(out, "sha256:", 7);
  for (size_t index = 0; index < 32; index++) {
    out[7 + index * 2] = digits[raw[index] >> 4];
    out[8 + index * 2] = digits[raw[index] & 15];
  }
  out[71] = 0;
}

static int held_regular(const char *path, int require_root, int forbid_write) {
  struct stat metadata;
  int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
  if (fd < 0 || fstat(fd, &metadata) || !S_ISREG(metadata.st_mode) || (require_root && metadata.st_uid != 0) || (forbid_write && (metadata.st_mode & 022))) {
    die("held artifact identity invalid");
  }
  return fd;
}

/* Runtime tools are exposed through /run/current-system, whose final path is
 * deliberately a root-owned symlink into the immutable Nix store. Open the
 * resolved object once and pin that descriptor; its exact digest is checked
 * before use. Mutable configuration and key material continue to use
 * held_regular() and therefore remain O_NOFOLLOW. */
static int held_runtime_regular(const char *path, int require_root, int forbid_write) {
  struct stat metadata;
  int fd = open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0 || fstat(fd, &metadata) || !S_ISREG(metadata.st_mode) || (require_root && metadata.st_uid != 0) ||
      (forbid_write && (metadata.st_mode & 022))) {
    die("held runtime artifact identity invalid");
  }
  return fd;
}

static void sha256_bytes(const unsigned char *raw, size_t size, char out[72]) {
  EVP_MD_CTX *context = EVP_MD_CTX_new();
  unsigned char digest[EVP_MAX_MD_SIZE];
  unsigned length = 0;
  if (!context || EVP_DigestInit_ex(context, EVP_sha256(), NULL) != 1 || EVP_DigestUpdate(context, raw, size) != 1 ||
      EVP_DigestFinal_ex(context, digest, &length) != 1 || length != 32) {
    die("SHA-256 failed");
  }
  EVP_MD_CTX_free(context);
  digest_from_raw(digest, out);
}

static void sha256_fd(int fd, char out[72]) {
  EVP_MD_CTX *context = EVP_MD_CTX_new();
  unsigned char digest[EVP_MAX_MD_SIZE], buffer[65536];
  unsigned length = 0;
  ssize_t count;
  if (!context || EVP_DigestInit_ex(context, EVP_sha256(), NULL) != 1) die("SHA-256 initialization failed");
  if (lseek(fd, 0, SEEK_SET) < 0) die("held artifact seek failed");
  while ((count = read(fd, buffer, sizeof(buffer))) > 0) {
    if (EVP_DigestUpdate(context, buffer, (size_t)count) != 1) die("SHA-256 update failed");
  }
  if (count < 0 || EVP_DigestFinal_ex(context, digest, &length) != 1 || length != 32) die("SHA-256 read failed");
  EVP_MD_CTX_free(context);
  if (lseek(fd, 0, SEEK_SET) < 0) die("held artifact reseek failed");
  digest_from_raw(digest, out);
}

static int pin_fd(const char *path, const char *expected, int target) {
  char observed[72];
#ifdef TGW_RENDER_TEST_BUILD
  int source = held_runtime_regular(path, 0, 1);
#else
  int source = held_runtime_regular(path, 1, 1);
#endif
  sha256_fd(source, observed);
  if (strcmp(observed, expected) || dup3(source, target, 0) < 0) die("held component pinning failed");
  close(source);
  return target;
}

static void parse_config(struct config *cfg) {
  enum { FIELD_COUNT = 20 };
  static const char *keys[FIELD_COUNT] = {
      "schema", "uid", "gid", "python", "python_sha256", "ip", "ip_sha256", "bootstrap", "bootstrap_sha256", "helper", "helper_sha256",
      "wrapper_sha256", "request_sha256", "prerequisite_receipt_sha256", "signing_key", "public_key_sha256", "packet_magic_hex", "packet_version", "max_output_bytes", "python_exe"};
  char raw[CONFIG_MAX + 1];
#ifdef TGW_RENDER_TEST_BUILD
  int fd = held_regular(CONFIG_PATH, 0, 1);
#else
  int fd = held_regular(CONFIG_PATH, 1, 1);
#endif
  ssize_t size = read(fd, raw, CONFIG_MAX + 1);
  close(fd);
  if (size <= 0 || size > CONFIG_MAX) die("configuration size invalid");
  raw[size] = 0;
  uint32_t seen = 0;
  size_t offset = 0;
  while (offset < (size_t)size) {
    size_t end = offset;
    while (end < (size_t)size && raw[end] != '\n') end++;
    if (end == offset || (end == (size_t)size && raw[end - 1] == '\r')) die("configuration line invalid");
    char *separator = memchr(raw + offset, '=', end - offset);
    if (!separator || separator == raw + offset || separator == raw + end - 1) die("configuration assignment invalid");
    size_t key_length = (size_t)(separator - (raw + offset));
    const char *value = separator + 1;
    size_t length = end - (size_t)(value - raw);
    int field = -1;
    for (int index = 0; index < FIELD_COUNT; index++) if (strlen(keys[index]) == key_length && !memcmp(raw + offset, keys[index], key_length)) field = index;
    if (field < 0) die("configuration key unknown");
    if (seen & (1U << field)) die("configuration key duplicated");
    seen |= 1U << field;
    switch (field) {
      case 0:
        if (length != 36 || memcmp(value, "tgw-nixos-observer-render-wrapper/v2", length)) die("configuration schema invalid");
        break;
      case 1: cfg->uid = (uid_t)parse_number(value, length, 4294967294UL); break;
      case 2: cfg->gid = (gid_t)parse_number(value, length, 4294967294UL); break;
      case 3: copy_path(cfg->python, value, length); break;
      case 4: copy_digest(cfg->python_sha256, value, length); break;
      case 5: copy_path(cfg->ip, value, length); break;
      case 6: copy_digest(cfg->ip_sha256, value, length); break;
      case 7: copy_path(cfg->bootstrap, value, length); break;
      case 8: copy_digest(cfg->bootstrap_sha256, value, length); break;
      case 9: copy_path(cfg->helper, value, length); break;
      case 10: copy_digest(cfg->helper_sha256, value, length); break;
      case 11: copy_digest(cfg->wrapper_sha256, value, length); break;
      case 12: copy_digest(cfg->request_sha256, value, length); break;
      case 13: copy_digest(cfg->prerequisite_sha256, value, length); break;
      case 14: copy_path(cfg->signing_key, value, length); break;
      case 15: copy_digest(cfg->public_key_sha256, value, length); break;
      case 16:
        if (length != 16 || !lower_hex(value, length)) die("configuration packet magic invalid");
        for (size_t index = 0; index < 8; index++) {
          unsigned high = value[index * 2] <= '9' ? (unsigned)(value[index * 2] - '0') : (unsigned)(value[index * 2] - 'a' + 10);
          unsigned low = value[index * 2 + 1] <= '9' ? (unsigned)(value[index * 2 + 1] - '0') : (unsigned)(value[index * 2 + 1] - 'a' + 10);
          cfg->packet_magic[index] = (unsigned char)((high << 4) | low);
        }
        break;
      case 17: cfg->packet_version = (uint32_t)parse_number(value, length, UINT32_MAX); break;
      case 18: cfg->max_output_bytes = (size_t)parse_number(value, length, 16UL * 1024UL * 1024UL); break;
      case 19: copy_path(cfg->python_exe, value, length); break;
    }
    offset = end + (end < (size_t)size ? 1 : 0);
  }
  if (seen != (1U << FIELD_COUNT) - 1U) die("configuration key missing");
}

static uint64_t packet_u64(const unsigned char *raw) {
  uint64_t value;
  memcpy(&value, raw, sizeof(value));
  return be64toh(value);
}

static void prefix_digest(const unsigned char *raw, size_t offset, char out[72]) {
  digest_from_raw(raw + offset, out);
}

static void copy_sha1(char out[41], const unsigned char *raw) {
  if (!lower_hex((const char *)raw, 40)) die("launch commit identity invalid");
  memcpy(out, raw, 40);
  out[40] = 0;
}

static void parse_launch(const unsigned char raw[LAUNCH_TRAILER], const unsigned char prefix[PACKET_PREFIX], struct launch_binding *binding) {
  uint32_t version;
  if (memcmp(raw, "TGWCTX01", 8)) die("launch binding magic invalid");
  memcpy(&version, raw + 8, sizeof(version));
  if (be32toh(version) != 1) die("launch binding version invalid");
  copy_sha1(binding->plan_commit, raw + 12);
  copy_sha1(binding->source_commit, raw + 52);
  copy_sha1(binding->source_tree, raw + 92);
  prefix_digest(prefix, 44, binding->request_sha256);
  digest_from_raw(raw + 132, binding->composition_sha256);
  static const char digits[] = "0123456789abcdef";
  for (size_t index = 0; index < 16; index++) {
    binding->attempt_id[index * 2] = digits[raw[164 + index] >> 4];
    binding->attempt_id[index * 2 + 1] = digits[raw[164 + index] & 15];
  }
  binding->attempt_id[32] = 0;
  size_t generation_size = 0;
  while (generation_size < 192 && raw[180 + generation_size]) generation_size++;
  if (!generation_size || generation_size >= 192) die("launch generation invalid");
  for (size_t index = generation_size; index < 192; index++) if (raw[180 + index]) die("launch generation padding invalid");
  for (size_t index = 0; index < generation_size; index++) {
    unsigned char byte = raw[180 + index];
    if (!((byte >= 'A' && byte <= 'Z') || (byte >= 'a' && byte <= 'z') || (byte >= '0' && byte <= '9') ||
          byte == '.' || byte == '_' || byte == ':' || byte == '@' || byte == '/' || byte == '-')) die("launch generation grammar invalid");
  }
  copy_bounded(binding->generation, sizeof(binding->generation), (const char *)raw + 180, generation_size);
}

static int spool_packet(const struct config *cfg, struct launch_binding *binding) {
  unsigned char prefix[PACKET_PREFIX], trailer[LAUNCH_TRAILER], buffer[65536];
  size_t consumed = 0, total = 0;
  ssize_t count;
  int packet = memfd_create("tgw-render-packet", MFD_CLOEXEC);
  if (packet < 0) die("packet memfd failed");
  while (consumed < sizeof(prefix)) {
    count = read(STDIN_FILENO, prefix + consumed, sizeof(prefix) - consumed);
    if (count <= 0) die("prepared packet prefix unavailable");
    consumed += (size_t)count;
  }
  if (memcmp(prefix, cfg->packet_magic, 8)) die("prepared packet magic invalid");
  uint32_t version;
  memcpy(&version, prefix + 8, sizeof(version));
  if (be32toh(version) != cfg->packet_version) die("prepared packet version invalid");
  uint64_t helper_size = packet_u64(prefix + 12), request_size = packet_u64(prefix + 20), tool_size = packet_u64(prefix + 28), archive_size = packet_u64(prefix + 36);
  if (!helper_size || !request_size || !tool_size || !archive_size || helper_size > 2UL * 1024UL * 1024UL || request_size > 1024UL * 1024UL ||
      tool_size > 1024UL * 1024UL || archive_size > 128UL * 1024UL * 1024UL) {
    die("prepared packet lengths invalid");
  }
  uint64_t expected = PACKET_PREFIX + helper_size + request_size + tool_size + archive_size;
  if (expected > MAX_PACKET) die("prepared packet exceeds wrapper bound");
  char request_digest[72], helper_digest[72];
  prefix_digest(prefix, 44, request_digest);
  prefix_digest(prefix, 76, helper_digest);
  if (strcmp(request_digest, cfg->request_sha256) || strcmp(helper_digest, cfg->helper_sha256)) die("prepared packet identity mismatch");
  if (write(packet, prefix, sizeof(prefix)) != (ssize_t)sizeof(prefix)) die("packet spool write failed");
  total = sizeof(prefix);
  while (total < expected) {
    size_t wanted = expected - total < sizeof(buffer) ? (size_t)(expected - total) : sizeof(buffer);
    count = read(STDIN_FILENO, buffer, wanted);
    if (count <= 0 || write(packet, buffer, (size_t)count) != count) die("prepared packet is truncated");
    total += (size_t)count;
  }
  consumed = 0;
  while (consumed < sizeof(trailer)) {
    count = read(STDIN_FILENO, trailer + consumed, sizeof(trailer) - consumed);
    if (count <= 0) die("launch binding is truncated");
    consumed += (size_t)count;
  }
  if (read(STDIN_FILENO, buffer, 1) != 0) die("prepared packet has trailing bytes");
  parse_launch(trailer, prefix, binding);
  if (strcmp(binding->request_sha256, cfg->request_sha256)) die("launch request binding mismatch");
  if (lseek(packet, 0, SEEK_SET) < 0) die("packet rewind failed");
  return packet;
}

static void require_empty_ip_output(int ipfd, char *const arguments[]) {
  int descriptors[2], status = 0;
  if (pipe2(descriptors, O_CLOEXEC)) die("probe pipe failed");
  pid_t child = fork();
  if (child < 0) die("probe fork failed");
  if (!child) {
    char executable[64];
    snprintf(executable, sizeof(executable), "/proc/self/fd/%d", ipfd);
    if (dup2(descriptors[1], STDOUT_FILENO) < 0) _exit(126);
    close(descriptors[0]);
    execve(executable, arguments, (char *const[]){"PATH=/var/empty", NULL});
    _exit(126);
  }
  close(descriptors[1]);
  char output[2];
  ssize_t count = read(descriptors[0], output, sizeof(output));
  close(descriptors[0]);
  if (waitpid(child, &status, 0) != child || status || count != 0) die("route-negative probe failed");
}

static void negative_probe(struct config *cfg, int ipfd) {
#ifdef TGW_RENDER_TEST_BUILD
  if (getenv("TGW_RENDER_TEST_SYSCALLS")) return;
#endif
  char state[32];
  FILE *loopback = fopen("/sys/class/net/lo/operstate", "re");
  if (!loopback || !fgets(state, sizeof(state), loopback) || strcmp(state, "down\n")) die("loopback-negative probe failed");
  fclose(loopback);
  DIR *network = opendir("/sys/class/net");
  struct dirent *entry;
  int links = 0;
  if (!network) die("link-negative probe unavailable");
  while ((entry = readdir(network))) {
    if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
    if (strcmp(entry->d_name, "lo")) die("unexpected link in isolated namespace");
    links++;
  }
  closedir(network);
  if (links != 1) die("isolated link inventory invalid");
  char *route4[] = {cfg->ip, "route", "show", NULL};
  char *route6[] = {cfg->ip, "-6", "route", "show", NULL};
  require_empty_ip_output(ipfd, route4);
  require_empty_ip_output(ipfd, route6);
  int probe = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
  struct sockaddr_in target = {.sin_family = AF_INET, .sin_port = htons(443)};
  if (probe < 0 || inet_pton(AF_INET, "203.0.113.1", &target.sin_addr) != 1) die("direct-negative probe setup failed");
  errno = 0;
  if (connect(probe, (struct sockaddr *)&target, sizeof(target)) == 0 || errno != ENETUNREACH) die("direct-negative probe did not deny egress");
  close(probe);
}

static void clear_capabilities(void) {
  FILE *source = fopen("/proc/sys/kernel/cap_last_cap", "re");
  int last = -1;
  if (!source || fscanf(source, "%d", &last) != 1 || last < 0 || last > 4096) die("capability inventory invalid");
  fclose(source);
  for (int capability = 0; capability <= last; capability++) {
    if (prctl(PR_CAPBSET_DROP, capability, 0, 0, 0) && errno != EINVAL) die("capability bounding-set drop failed");
  }
  if (prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0)) die("ambient capability clear failed");
  struct __user_cap_header_struct header = {_LINUX_CAPABILITY_VERSION_3, 0};
  struct __user_cap_data_struct data[2] = {{0}};
  if (syscall(SYS_capset, &header, data)) die("capability set clear failed");
}

static void drop_identity(const struct config *cfg) {
#ifdef TGW_RENDER_TEST_BUILD
  if (getenv("TGW_RENDER_TEST_SYSCALLS")) {
    if (getuid() != cfg->uid || getgid() != cfg->gid) die("injected identity mismatch");
    return;
  }
#endif
  if (setgroups(0, NULL)) die("supplementary group drop failed");
  unsigned secure = SECBIT_NOROOT | SECBIT_NOROOT_LOCKED | SECBIT_NO_SETUID_FIXUP | SECBIT_NO_SETUID_FIXUP_LOCKED;
  if (prctl(PR_SET_SECUREBITS, secure, 0, 0, 0) || setresgid(cfg->gid, cfg->gid, cfg->gid) || setresuid(cfg->uid, cfg->uid, cfg->uid)) {
    die("unprivileged identity drop failed");
  }
  clear_capabilities();
  if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) die("no-new-privileges failed");
  if (getuid() != cfg->uid || geteuid() != cfg->uid || getgid() != cfg->gid || getegid() != cfg->gid) die("post-drop identity mismatch");
}

static uint64_t process_starttime(pid_t pid) {
  char path[64], raw[4096];
  snprintf(path, sizeof(path), "/proc/%ld/stat", (long)pid);
  int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
  if (fd < 0) die("child stat unavailable");
  ssize_t length = read(fd, raw, sizeof(raw) - 1);
  close(fd);
  if (length <= 0 || length >= (ssize_t)sizeof(raw) - 1) die("child stat invalid");
  raw[length] = 0;
  char *cursor = strrchr(raw, ')');
  if (!cursor || cursor[1] != ' ') die("child stat grammar invalid");
  cursor += 2;
  for (int field = 3; field < 22; field++) {
    cursor = strchr(cursor, ' ');
    if (!cursor) die("child starttime absent");
    cursor++;
  }
  char *end = NULL;
  errno = 0;
  unsigned long long value = strtoull(cursor, &end, 10);
  if (errno || !value || !end || (*end != ' ' && *end != '\n' && *end)) die("child starttime invalid");
  return (uint64_t)value;
}

static void process_exe(pid_t pid, const char *expected, char out[4096]) {
  char path[64];
  snprintf(path, sizeof(path), "/proc/%ld/exe", (long)pid);
  for (int attempt = 0; attempt < 10000; attempt++) {
    ssize_t length = readlink(path, out, 4095);
    if (length > 0 && length < 4095) {
      out[length] = 0;
      if (!strcmp(out, expected)) return;
    }
    sched_yield();
  }
  die("child executable identity unavailable");
}

static unsigned char *read_child(int descriptor, size_t maximum, size_t *length) {
  size_t capacity = 65536, used = 0;
  unsigned char *raw = malloc(capacity);
  if (!raw) die("terminal buffer allocation failed");
  for (;;) {
    if (used == capacity) {
      if (capacity >= maximum) die("child terminal exceeded bound");
      size_t next = capacity * 2 > maximum ? maximum : capacity * 2;
      unsigned char *grown = realloc(raw, next);
      if (!grown) die("terminal buffer growth failed");
      raw = grown;
      capacity = next;
    }
    ssize_t count = read(descriptor, raw + used, capacity - used);
    if (count < 0) die("child terminal read failed");
    if (!count) break;
    used += (size_t)count;
    if (used > maximum) die("child terminal exceeded bound");
  }
  if (!used) die("child terminal absent");
  *length = used;
  return raw;
}

static EVP_PKEY *open_signing_key(const struct config *cfg) {
#ifdef TGW_RENDER_TEST_BUILD
  int descriptor = held_regular(cfg->signing_key, 0, 1);
#else
  int descriptor = held_regular(cfg->signing_key, 1, 1);
#endif
  FILE *stream = fdopen(descriptor, "re");
  if (!stream) die("signing-key stream failed");
  EVP_PKEY *key = PEM_read_PrivateKey(stream, NULL, NULL, NULL);
  fclose(stream);
  if (!key || EVP_PKEY_id(key) != EVP_PKEY_ED25519) die("signing key is not Ed25519");
  unsigned char public_key[32];
  size_t public_size = sizeof(public_key);
  char digest[72];
  if (EVP_PKEY_get_raw_public_key(key, public_key, &public_size) != 1 || public_size != sizeof(public_key)) die("signing public key unavailable");
  sha256_bytes(public_key, public_size, digest);
  if (strcmp(digest, cfg->public_key_sha256)) die("signing public key identity mismatch");
  return key;
}

static void append_field(unsigned char *payload, size_t capacity, size_t *used, const char *field) {
  size_t length = strlen(field);
  if (*used + length + (*used ? 1 : 0) > capacity) die("attestation payload overflow");
  if (*used) payload[(*used)++] = 0;
  memcpy(payload + *used, field, length);
  *used += length;
}

static char *base64_encode(const unsigned char *raw, size_t length) {
  size_t capacity = 4 * ((length + 2) / 3) + 1;
  char *encoded = malloc(capacity);
  if (!encoded || EVP_EncodeBlock((unsigned char *)encoded, raw, (int)length) < 0) die("base64 encoding failed");
  return encoded;
}

static void emit_envelope(
    const struct config *cfg, EVP_PKEY *key, const struct launch_binding *launch, const char *before, const char *after,
    const unsigned char *terminal, size_t terminal_size, int returncode, pid_t child_pid, uint64_t child_starttime, const char *child_exe) {
  char terminal_sha[72], probe_sha[72], uid[32], gid[32], status[32], bytes[32], pid[32], starttime[32], issued[32], expires[32], nonce[65];
  unsigned char nonce_raw[32];
  size_t nonce_used = 0;
  while (nonce_used < sizeof(nonce_raw)) {
    ssize_t count = getrandom(nonce_raw + nonce_used, sizeof(nonce_raw) - nonce_used, 0);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) die("attestation nonce generation failed");
    nonce_used += (size_t)count;
  }
  static const char digits[] = "0123456789abcdef";
  for (size_t index = 0; index < sizeof(nonce_raw); index++) {
    nonce[index * 2] = digits[nonce_raw[index] >> 4];
    nonce[index * 2 + 1] = digits[nonce_raw[index] & 15];
  }
  nonce[64] = 0;
  struct timespec wall;
  if (clock_gettime(CLOCK_REALTIME, &wall) || wall.tv_sec <= 0) die("attestation clock unavailable");
  sha256_bytes(terminal, terminal_size, terminal_sha);
  sha256_bytes((const unsigned char *)PROBE_JSON, strlen(PROBE_JSON), probe_sha);
  snprintf(uid, sizeof(uid), "%lu", (unsigned long)cfg->uid);
  snprintf(gid, sizeof(gid), "%lu", (unsigned long)cfg->gid);
  snprintf(status, sizeof(status), "%d", returncode);
  snprintf(bytes, sizeof(bytes), "%zu", terminal_size);
  snprintf(pid, sizeof(pid), "%ld", (long)child_pid);
  snprintf(starttime, sizeof(starttime), "%llu", (unsigned long long)child_starttime);
  snprintf(issued, sizeof(issued), "%lld", (long long)wall.tv_sec);
  snprintf(expires, sizeof(expires), "%lld", (long long)wall.tv_sec + ENVELOPE_TTL);
  unsigned char payload[8192];
  size_t payload_size = 0;
  const char *fields[] = {
      "tgw-nixos-observer-render-wrapper-envelope/v2", launch->plan_commit, launch->source_commit, launch->source_tree, cfg->request_sha256,
      launch->generation, launch->composition_sha256, launch->attempt_id, nonce, issued, expires,
#ifdef TGW_RENDER_TEST_BUILD
      "1",
#else
      "0",
#endif
      cfg->helper_sha256, cfg->bootstrap_sha256, cfg->python_sha256, cfg->ip_sha256,
      cfg->wrapper_sha256, cfg->prerequisite_sha256,
      before, after, "1", probe_sha, probe_sha, pid, starttime, child_exe, uid, gid, status, bytes, terminal_sha};
  for (size_t index = 0; index < sizeof(fields) / sizeof(fields[0]); index++) append_field(payload, sizeof(payload), &payload_size, fields[index]);
  EVP_MD_CTX *context = EVP_MD_CTX_new();
  size_t signature_size = 0;
  if (!context || EVP_DigestSignInit(context, NULL, NULL, NULL, key) != 1 || EVP_DigestSign(context, NULL, &signature_size, payload, payload_size) != 1) {
    die("attestation signature initialization failed");
  }
  unsigned char *signature = malloc(signature_size);
  if (!signature || EVP_DigestSign(context, signature, &signature_size, payload, payload_size) != 1) die("attestation signing failed");
  EVP_MD_CTX_free(context);
  char *terminal_b64 = base64_encode(terminal, terminal_size), *signature_b64 = base64_encode(signature, signature_size);
  dprintf(STDOUT_FILENO,
      "{\"schema\":\"tgw-nixos-observer-render-wrapper-envelope/v2\",\"plan_commit\":\"%s\",\"source_commit\":\"%s\",\"source_tree\":\"%s\","
      "\"request_sha256\":\"%s\",\"effect_generation\":\"%s\",\"composition_sha256\":\"%s\",\"attempt_id\":\"%s\","
      "\"nonce\":\"%s\",\"issued_at\":%s,\"expires_at\":%s,"
#ifdef TGW_RENDER_TEST_BUILD
      "\"test_build\":true,"
#else
      "\"test_build\":false,"
#endif
      "\"helper_sha256\":\"%s\","
      "\"remote_bootstrap_sha256\":\"%s\",\"remote_python_sha256\":\"%s\",\"remote_ip_sha256\":\"%s\","
      "\"wrapper_sha256\":\"%s\",\"wrapper_prerequisite_receipt_sha256\":\"%s\",\"namespace\":{\"schema\":\"tgw-render-network-namespace-evidence/v1\","
      "\"before\":\"%s\",\"after\":\"%s\",\"changed\":true,\"pre\":%s,\"post\":%s},\"child\":{\"pid\":%s,\"starttime\":%s,\"exe\":\"%s\",\"uid\":%s,\"gid\":%s,\"returncode\":%s,"
      "\"terminal_bytes\":%s,\"terminal_sha256\":\"%s\",\"terminal_b64\":\"%s\"},\"attestation\":{\"algorithm\":\"ed25519\","
      "\"public_key_sha256\":\"%s\",\"signature\":\"%s\"}}",
      launch->plan_commit, launch->source_commit, launch->source_tree, cfg->request_sha256, launch->generation, launch->composition_sha256,
      launch->attempt_id, nonce, issued, expires, cfg->helper_sha256, cfg->bootstrap_sha256, cfg->python_sha256, cfg->ip_sha256,
      cfg->wrapper_sha256, cfg->prerequisite_sha256, before,
      after, PROBE_JSON, PROBE_JSON, pid, starttime, child_exe, uid, gid, status, bytes,
      terminal_sha, terminal_b64, cfg->public_key_sha256, signature_b64);
  free(signature_b64);
  free(terminal_b64);
  free(signature);
}

int main(int argc, char **argv) {
  if (argc != 1 || argv[1]) die("arguments forbidden");
  struct config cfg = {0};
  parse_config(&cfg);
#ifdef TGW_RENDER_TEST_BUILD
  if (getenv("TGW_RENDER_TEST_PARSE_ONLY")) return 0;
#endif
  struct stat self_metadata;
  int self = open("/proc/self/exe", O_RDONLY | O_CLOEXEC);
  if (self < 0 || fstat(self, &self_metadata) || !S_ISREG(self_metadata.st_mode) ||
#ifndef TGW_RENDER_TEST_BUILD
      self_metadata.st_uid != 0 ||
#endif
      (self_metadata.st_mode & 022)) {
    die("wrapper self inode invalid");
  }
  char self_digest[72];
  sha256_fd(self, self_digest);
  close(self);
  if (strcmp(self_digest, cfg.wrapper_sha256)) die("wrapper self-identity mismatch");
  char resolved_python[4096];
  if (!realpath(cfg.python, resolved_python) || strcmp(resolved_python, cfg.python_exe)) die("python executable path binding invalid");
  int pythonfd = pin_fd(cfg.python, cfg.python_sha256, 200);
  int ipfd = pin_fd(cfg.ip, cfg.ip_sha256, 201);
  int bootstrapfd = pin_fd(cfg.bootstrap, cfg.bootstrap_sha256, 202);
  int helperfd = pin_fd(cfg.helper, cfg.helper_sha256, 203);
  struct launch_binding launch = {0};
  int packetfd = spool_packet(&cfg, &launch);
  EVP_PKEY *signing_key = open_signing_key(&cfg);
  char before[64], after[64];
  ssize_t before_size = readlink("/proc/self/ns/net", before, sizeof(before) - 1);
  if (before_size <= 0 || before_size >= (ssize_t)sizeof(before) - 1) die("initial namespace identity unavailable");
  before[before_size] = 0;
#ifdef TGW_RENDER_TEST_BUILD
  int injected = getenv("TGW_RENDER_TEST_SYSCALLS") != NULL;
  if (injected) {
    memcpy(before, "net:[301]", 10);
  } else
#endif
  if (unshare(CLONE_NEWNET)) die("network namespace creation failed");
  ssize_t after_size;
#ifdef TGW_RENDER_TEST_BUILD
  if (injected) {
    memcpy(after, "net:[302]", 10);
    after_size = 9;
  } else
#endif
  after_size = readlink("/proc/self/ns/net", after, sizeof(after) - 1);
  if (after_size <= 0 || after_size >= (ssize_t)sizeof(after) - 1) die("isolated namespace identity unavailable");
  after[after_size] = 0;
  if (!strcmp(before, after)) die("network namespace did not change");
  negative_probe(&cfg, ipfd);
  int output[2];
  if (pipe2(output, O_CLOEXEC)) die("child output pipe failed");
  pid_t child = fork();
  if (child < 0) die("child fork failed");
  if (!child) {
    close(output[0]);
    if (dup2(packetfd, STDIN_FILENO) < 0 || dup2(output[1], STDOUT_FILENO) < 0) _exit(126);
    close(output[1]);
    drop_identity(&cfg);
    char python[64], bootstrap[64], helper_descriptor[32], helper_environment[128];
    snprintf(python, sizeof(python), "/proc/self/fd/%d", pythonfd);
    snprintf(bootstrap, sizeof(bootstrap), "/proc/self/fd/%d", bootstrapfd);
    snprintf(helper_descriptor, sizeof(helper_descriptor), "TGW_RENDER_HELPER_FD=%d", helperfd);
    snprintf(helper_environment, sizeof(helper_environment), "TGW_RENDER_HELPER_SHA256=%s", cfg.helper_sha256);
    char *arguments[] = {python, "-I", bootstrap, NULL};
    char *environment[] = {"HOME=/var/empty", "PATH=/var/empty", "LC_ALL=C", helper_descriptor, helper_environment, NULL};
    execve(python, arguments, environment);
    _exit(126);
  }
  uint64_t child_starttime = process_starttime(child);
  char child_exe[4096];
  process_exe(child, cfg.python_exe, child_exe);
  close(output[1]);
  close(packetfd);
  size_t terminal_size = 0;
  unsigned char *terminal = read_child(output[0], cfg.max_output_bytes, &terminal_size);
  close(output[0]);
  int wait_status = 0;
  if (waitpid(child, &wait_status, 0) != child) die("child wait failed");
  int returncode = WIFEXITED(wait_status) ? WEXITSTATUS(wait_status) : WIFSIGNALED(wait_status) ? 128 + WTERMSIG(wait_status) : 125;
  negative_probe(&cfg, ipfd);
  emit_envelope(&cfg, signing_key, &launch, before, after, terminal, terminal_size, returncode, child, child_starttime, child_exe);
  EVP_PKEY_free(signing_key);
  free(terminal);
  return returncode;
}
