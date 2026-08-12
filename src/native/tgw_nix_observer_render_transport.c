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
#define MAX_PACKET (128UL * 1024UL * 1024UL + 4UL * 1024UL * 1024UL)
#define PROBE_JSON "{\"direct_probe\":\"ENETUNREACH\",\"ipv4_route_count\":0,\"ipv6_route_count\":0,\"links\":[\"lo\"],\"loopback_state\":\"down\",\"schema\":\"tgw-render-netns-negative-probe/v1\"}"

struct config {
  uid_t uid;
  gid_t gid;
  char python[4096], ip[4096], bootstrap[4096], helper[4096], signing_key[4096];
  char python_sha256[72], ip_sha256[72], bootstrap_sha256[72], helper_sha256[72];
  char wrapper_sha256[72], request_sha256[72], prerequisite_sha256[72], public_key_sha256[72];
  size_t max_output_bytes;
};

static void die(const char *message) {
  dprintf(STDERR_FILENO, "tgw-render-wrapper: %s\n", message);
  _exit(125);
}

static void copy_value(char *out, size_t size, const char *line, const char *key) {
  size_t n = strlen(key), length;
  if (strncmp(line, key, n) || line[n] != '=') die("configuration key mismatch");
  const char *value = line + n + 1;
  length = strlen(value);
  if (!length || length >= size || strchr(value, '\r') || strchr(value, '\n')) die("configuration value invalid");
  memcpy(out, value, length + 1);
}

static unsigned long parse_number(const char *value, unsigned long maximum) {
  char *end = NULL;
  errno = 0;
  unsigned long result = strtoul(value, &end, 10);
  if (errno || !result || !end || *end || result > maximum) die("configuration number invalid");
  return result;
}

static int held_regular(const char *path, int require_root, int forbid_write) {
  struct stat metadata;
  int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
  if (fd < 0 || fstat(fd, &metadata) || !S_ISREG(metadata.st_mode) || (require_root && metadata.st_uid != 0) || (forbid_write && (metadata.st_mode & 022))) {
    die("held artifact identity invalid");
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
  strcpy(out, "sha256:");
  for (size_t index = 0; index < length; index++) {
    if (snprintf(out + 7 + index * 2, 72 - (7 + index * 2), "%02x", digest[index]) != 2) die("SHA-256 formatting failed");
  }
  out[71] = 0;
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
  strcpy(out, "sha256:");
  for (size_t index = 0; index < length; index++) {
    if (snprintf(out + 7 + index * 2, 72 - (7 + index * 2), "%02x", digest[index]) != 2) die("SHA-256 formatting failed");
  }
  out[71] = 0;
}

static int pin_fd(const char *path, const char *expected, int target) {
  char observed[72];
  int source = held_regular(path, 1, 1);
  sha256_fd(source, observed);
  if (strcmp(observed, expected) || dup3(source, target, 0) < 0) die("held component pinning failed");
  close(source);
  return target;
}

static void parse_config(struct config *cfg) {
  char raw[CONFIG_MAX + 1], value[4096], *save = NULL, *line;
  int fd = held_regular(CONFIG_PATH, 1, 1);
  ssize_t size = read(fd, raw, CONFIG_MAX + 1);
  close(fd);
  if (size <= 0 || size > CONFIG_MAX) die("configuration size invalid");
  raw[size] = 0;
  const char *keys[] = {
      "schema", "uid", "gid", "python", "python_sha256", "ip", "ip_sha256", "bootstrap", "bootstrap_sha256", "helper", "helper_sha256",
      "wrapper_sha256", "request_sha256", "prerequisite_receipt_sha256", "signing_key", "public_key_sha256", "max_output_bytes"};
  line = strtok_r(raw, "\n", &save);
  for (size_t index = 0; index < sizeof(keys) / sizeof(keys[0]); index++) {
    if (!line) die("configuration truncated");
    copy_value(value, sizeof(value), line, keys[index]);
    switch (index) {
      case 0: if (strcmp(value, "tgw-nixos-observer-render-wrapper/v1")) die("configuration schema invalid"); break;
      case 1: cfg->uid = (uid_t)parse_number(value, 4294967294UL); break;
      case 2: cfg->gid = (gid_t)parse_number(value, 4294967294UL); break;
      case 3: strcpy(cfg->python, value); break;
      case 4: strcpy(cfg->python_sha256, value); break;
      case 5: strcpy(cfg->ip, value); break;
      case 6: strcpy(cfg->ip_sha256, value); break;
      case 7: strcpy(cfg->bootstrap, value); break;
      case 8: strcpy(cfg->bootstrap_sha256, value); break;
      case 9: strcpy(cfg->helper, value); break;
      case 10: strcpy(cfg->helper_sha256, value); break;
      case 11: strcpy(cfg->wrapper_sha256, value); break;
      case 12: strcpy(cfg->request_sha256, value); break;
      case 13: strcpy(cfg->prerequisite_sha256, value); break;
      case 14: strcpy(cfg->signing_key, value); break;
      case 15: strcpy(cfg->public_key_sha256, value); break;
      case 16: cfg->max_output_bytes = (size_t)parse_number(value, 16UL * 1024UL * 1024UL); break;
    }
    line = strtok_r(NULL, "\n", &save);
  }
  if (line || cfg->python[0] != '/' || cfg->ip[0] != '/' || cfg->bootstrap[0] != '/' || cfg->helper[0] != '/' || cfg->signing_key[0] != '/') {
    die("configuration is not closed");
  }
}

static uint64_t packet_u64(const unsigned char *raw) {
  uint64_t value;
  memcpy(&value, raw, sizeof(value));
  return be64toh(value);
}

static void prefix_digest(const unsigned char *raw, size_t offset, char out[72]) {
  strcpy(out, "sha256:");
  for (size_t index = 0; index < 32; index++) {
    if (snprintf(out + 7 + index * 2, 72 - (7 + index * 2), "%02x", raw[offset + index]) != 2) die("prefix digest formatting failed");
  }
  out[71] = 0;
}

static int spool_packet(const struct config *cfg) {
  unsigned char prefix[PACKET_PREFIX], buffer[65536];
  size_t consumed = 0, total = 0;
  ssize_t count;
  int packet = memfd_create("tgw-render-packet", MFD_CLOEXEC);
  if (packet < 0) die("packet memfd failed");
  while (consumed < sizeof(prefix)) {
    count = read(STDIN_FILENO, prefix + consumed, sizeof(prefix) - consumed);
    if (count <= 0) die("prepared packet prefix unavailable");
    consumed += (size_t)count;
  }
  if (memcmp(prefix, "TGWNIXO1", 8)) die("prepared packet magic invalid");
  uint32_t version;
  memcpy(&version, prefix + 8, sizeof(version));
  if (be32toh(version) != 1) die("prepared packet version invalid");
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
  if (read(STDIN_FILENO, buffer, 1) != 0) die("prepared packet has trailing bytes");
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
  if (setgroups(0, NULL)) die("supplementary group drop failed");
  unsigned secure = SECBIT_NOROOT | SECBIT_NOROOT_LOCKED | SECBIT_NO_SETUID_FIXUP | SECBIT_NO_SETUID_FIXUP_LOCKED;
  if (prctl(PR_SET_SECUREBITS, secure, 0, 0, 0) || setresgid(cfg->gid, cfg->gid, cfg->gid) || setresuid(cfg->uid, cfg->uid, cfg->uid)) {
    die("unprivileged identity drop failed");
  }
  clear_capabilities();
  if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) die("no-new-privileges failed");
  if (getuid() != cfg->uid || geteuid() != cfg->uid || getgid() != cfg->gid || getegid() != cfg->gid) die("post-drop identity mismatch");
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
  int descriptor = held_regular(cfg->signing_key, 1, 1);
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

static void emit_envelope(const struct config *cfg, EVP_PKEY *key, const char *before, const char *after, const unsigned char *terminal, size_t terminal_size, int returncode) {
  char terminal_sha[72], probe_sha[72], uid[32], gid[32], status[32], bytes[32];
  sha256_bytes(terminal, terminal_size, terminal_sha);
  sha256_bytes((const unsigned char *)PROBE_JSON, strlen(PROBE_JSON), probe_sha);
  snprintf(uid, sizeof(uid), "%lu", (unsigned long)cfg->uid);
  snprintf(gid, sizeof(gid), "%lu", (unsigned long)cfg->gid);
  snprintf(status, sizeof(status), "%d", returncode);
  snprintf(bytes, sizeof(bytes), "%zu", terminal_size);
  unsigned char payload[4096];
  size_t payload_size = 0;
  const char *fields[] = {
      "tgw-nixos-observer-render-wrapper-envelope/v1", cfg->request_sha256, cfg->helper_sha256, cfg->bootstrap_sha256, cfg->python_sha256, cfg->ip_sha256,
      cfg->wrapper_sha256, cfg->prerequisite_sha256,
      before, after, "1", probe_sha, probe_sha, uid, gid, status, bytes, terminal_sha};
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
      "{\"schema\":\"tgw-nixos-observer-render-wrapper-envelope/v1\",\"request_sha256\":\"%s\",\"helper_sha256\":\"%s\","
      "\"remote_bootstrap_sha256\":\"%s\",\"remote_python_sha256\":\"%s\",\"remote_ip_sha256\":\"%s\","
      "\"wrapper_sha256\":\"%s\",\"wrapper_prerequisite_receipt_sha256\":\"%s\",\"namespace\":{\"schema\":\"tgw-render-network-namespace-evidence/v1\","
      "\"before\":\"%s\",\"after\":\"%s\",\"changed\":true,\"pre\":%s,\"post\":%s},\"child\":{\"uid\":%s,\"gid\":%s,\"returncode\":%s,"
      "\"terminal_bytes\":%s,\"terminal_sha256\":\"%s\",\"terminal_b64\":\"%s\"},\"attestation\":{\"algorithm\":\"ed25519\","
      "\"public_key_sha256\":\"%s\",\"signature\":\"%s\"}}",
      cfg->request_sha256, cfg->helper_sha256, cfg->bootstrap_sha256, cfg->python_sha256, cfg->ip_sha256, cfg->wrapper_sha256, cfg->prerequisite_sha256, before,
      after, PROBE_JSON, PROBE_JSON, uid, gid, status, bytes,
      terminal_sha, terminal_b64, cfg->public_key_sha256, signature_b64);
  free(signature_b64);
  free(terminal_b64);
  free(signature);
}

int main(int argc, char **argv) {
  if (argc != 1 || argv[1]) die("arguments forbidden");
  struct config cfg = {0};
  parse_config(&cfg);
  struct stat self_metadata;
  int self = open("/proc/self/exe", O_RDONLY | O_CLOEXEC);
  if (self < 0 || fstat(self, &self_metadata) || !S_ISREG(self_metadata.st_mode) || self_metadata.st_uid != 0 || (self_metadata.st_mode & 022)) {
    die("wrapper self inode invalid");
  }
  char self_digest[72];
  sha256_fd(self, self_digest);
  close(self);
  if (strcmp(self_digest, cfg.wrapper_sha256)) die("wrapper self-identity mismatch");
  int pythonfd = pin_fd(cfg.python, cfg.python_sha256, 200);
  int ipfd = pin_fd(cfg.ip, cfg.ip_sha256, 201);
  int bootstrapfd = pin_fd(cfg.bootstrap, cfg.bootstrap_sha256, 202);
  int helperfd = pin_fd(cfg.helper, cfg.helper_sha256, 203);
  int packetfd = spool_packet(&cfg);
  EVP_PKEY *signing_key = open_signing_key(&cfg);
  char before[64], after[64];
  ssize_t before_size = readlink("/proc/self/ns/net", before, sizeof(before) - 1);
  if (before_size <= 0 || before_size >= (ssize_t)sizeof(before) - 1) die("initial namespace identity unavailable");
  before[before_size] = 0;
  if (unshare(CLONE_NEWNET)) die("network namespace creation failed");
  ssize_t after_size = readlink("/proc/self/ns/net", after, sizeof(after) - 1);
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
  close(output[1]);
  close(packetfd);
  size_t terminal_size = 0;
  unsigned char *terminal = read_child(output[0], cfg.max_output_bytes, &terminal_size);
  close(output[0]);
  int wait_status = 0;
  if (waitpid(child, &wait_status, 0) != child) die("child wait failed");
  int returncode = WIFEXITED(wait_status) ? WEXITSTATUS(wait_status) : WIFSIGNALED(wait_status) ? 128 + WTERMSIG(wait_status) : 125;
  negative_probe(&cfg, ipfd);
  emit_envelope(&cfg, signing_key, before, after, terminal, terminal_size, returncode);
  EVP_PKEY_free(signing_key);
  free(terminal);
  return returncode;
}
