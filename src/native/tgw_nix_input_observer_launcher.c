#define _GNU_SOURCE
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/capability.h>
#include <linux/securebits.h>
#include <openssl/evp.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef TGW_LAUNCHER_DESCRIPTOR
#define TGW_LAUNCHER_DESCRIPTOR "/etc/tgw/nix-input-observer-launcher.conf"
#endif
#define DESCRIPTOR TGW_LAUNCHER_DESCRIPTOR
#define MAX_DESCRIPTOR 16384

struct config {
  uid_t uid; gid_t gid;
  char python[4096], ip[4096], observer[4096], nix[4096], nix_store[4096], git[4096], cgroup[4096];
  char launcher_sha256[72], python_sha256[72], ip_sha256[72], observer_sha256[72], nix_sha256[72], nix_store_sha256[72], git_sha256[72], request_sha256[72], descriptor_sha256[72], transport_config_sha256[72];
};

static void die(const char *message) { dprintf(2, "tgw-observer-launcher: %s\n", message); _exit(125); }

static void copy_value(char *out, size_t size, const char *line, const char *key) {
  size_t n = strlen(key);
  if (strncmp(line, key, n) || line[n] != '=') die("descriptor key mismatch");
  const char *value = line + n + 1;
  if (!*value || strlen(value) >= size || strchr(value, '\n') || strchr(value, '\r')) die("descriptor value invalid");
  memcpy(out, value, strlen(value) + 1);
}

static unsigned long parse_id(const char *value) {
  char *end=NULL; errno=0; unsigned long result=strtoul(value,&end,10);
  if(errno || !result || !end || *end || result>4294967294UL) die("numeric descriptor value invalid");
  return result;
}

static int held_regular(const char *path) {
  int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
  struct stat st;
  if (fd < 0 || fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_uid != 0 || (st.st_mode & 022)) die("immutable artifact invalid");
  return fd;
}

static void sha256_fd(int fd, char out[72]) {
  EVP_MD_CTX *ctx=EVP_MD_CTX_new(); unsigned char digest[EVP_MAX_MD_SIZE], buffer[65536]; unsigned length=0; ssize_t n;
  if(!ctx || EVP_DigestInit_ex(ctx,EVP_sha256(),NULL)!=1) die("digest initialization failed");
  lseek(fd,0,SEEK_SET);
  while((n=read(fd,buffer,sizeof(buffer)))>0) if(EVP_DigestUpdate(ctx,buffer,(size_t)n)!=1) die("digest update failed");
  if(n<0) die("artifact digest read failed");
  if(EVP_DigestFinal_ex(ctx,digest,&length)!=1 || length!=32) die("digest finalization failed");
  EVP_MD_CTX_free(ctx); lseek(fd,0,SEEK_SET);
  strcpy(out,"sha256:");
  for(size_t i=0;i<length;i++) if(snprintf(out+7+i*2,72-(7+i*2),"%02x",digest[i])!=2) die("digest formatting failed");
  out[71]=0;
}

#ifdef TGW_LAUNCHER_DIGEST_TEST
int main(int argc, char **argv) {
  if(argc!=2) return 2;
  int fd=open(argv[1],O_RDONLY|O_NOFOLLOW|O_CLOEXEC); char digest[72];
  if(fd<0) return 3; sha256_fd(fd,digest); close(fd); puts(digest); return 0;
}
#else

static void parse_descriptor(struct config *cfg) {
  int fd = held_regular(DESCRIPTOR);
  sha256_fd(fd, cfg->descriptor_sha256);
  char raw[MAX_DESCRIPTOR + 1];
  ssize_t n = read(fd, raw, MAX_DESCRIPTOR + 1);
  if (n <= 0 || n > MAX_DESCRIPTOR) die("descriptor size invalid");
  raw[n] = 0; close(fd);
  char *save = NULL, *line = strtok_r(raw, "\n", &save), value[4096];
  const char *keys[] = {"schema", "uid", "gid", "python", "ip", "observer", "nix", "nix_store", "git", "launcher_sha256", "python_sha256", "ip_sha256", "observer_sha256", "nix_sha256", "nix_store_sha256", "git_sha256", "request_sha256", "transport_config_sha256", "observer_cgroup"};
  for (size_t i = 0; i < sizeof(keys)/sizeof(keys[0]); i++) {
    if (!line) die("descriptor truncated");
    copy_value(value, sizeof(value), line, keys[i]);
    if (i == 0 && strcmp(value, "tgw-nix-input-observer-launcher/v2")) die("descriptor schema invalid");
    else if (i == 1) cfg->uid = (uid_t)parse_id(value);
    else if (i == 2) cfg->gid = (gid_t)parse_id(value);
    else if (i == 3) strcpy(cfg->python, value);
    else if (i == 4) strcpy(cfg->ip, value);
    else if (i == 5) strcpy(cfg->observer, value);
    else if (i == 6) strcpy(cfg->nix, value);
    else if (i == 7) strcpy(cfg->nix_store, value);
    else if (i == 8) strcpy(cfg->git, value);
    else if (i == 9) strcpy(cfg->launcher_sha256, value);
    else if (i == 10) strcpy(cfg->python_sha256, value);
    else if (i == 11) strcpy(cfg->ip_sha256, value);
    else if (i == 12) strcpy(cfg->observer_sha256, value);
    else if (i == 13) strcpy(cfg->nix_sha256, value);
    else if (i == 14) strcpy(cfg->nix_store_sha256, value);
    else if (i == 15) strcpy(cfg->git_sha256, value);
    else if (i == 16) strcpy(cfg->request_sha256, value);
    else if (i == 17) strcpy(cfg->transport_config_sha256, value);
    else strcpy(cfg->cgroup, value);
    line = strtok_r(NULL, "\n", &save);
  }
  if (line || !cfg->uid || !cfg->gid || cfg->python[0] != '/' || cfg->ip[0] != '/' || cfg->observer[0] != '/' || cfg->nix[0]!='/' || cfg->nix_store[0]!='/' || cfg->git[0]!='/' || strncmp(cfg->cgroup, "0::/", 4)) die("descriptor not closed");
}

static void require_empty_ip_output(int ipfd, char *const args[]) {
  int pipefd[2]; if(pipe2(pipefd,O_CLOEXEC)) die("pipe failed");
  pid_t child = fork(); int status = 0;
  if (child < 0) die("fork failed");
  if (!child) { dup2(pipefd[1],1); close(pipefd[0]); char path[64]; snprintf(path, sizeof(path), "/proc/self/fd/%d", ipfd); execve(path, args, (char *const[]){"PATH=/var/empty", NULL}); _exit(126); }
  close(pipefd[1]); char output[2]; ssize_t n=read(pipefd[0],output,sizeof(output)); close(pipefd[0]);
  if (waitpid(child, &status, 0) != child || status) die("network namespace verification failed");
  if(n!=0) die("network namespace has routes");
}

static void clear_capabilities(void) {
  FILE *f = fopen("/proc/sys/kernel/cap_last_cap", "re"); int last = -1;
  if (!f || fscanf(f, "%d", &last) != 1 || last < 0 || last > 4096) die("cap_last_cap invalid");
  fclose(f);
  for (int cap = 0; cap <= last; cap++) if (prctl(PR_CAPBSET_DROP, cap, 0, 0, 0) && errno != EINVAL) die("bounding capability drop failed");
  if (prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0)) die("ambient capability clear failed");
  struct __user_cap_header_struct header = {_LINUX_CAPABILITY_VERSION_3, 0};
  struct __user_cap_data_struct data[2] = {{0}};
  if (syscall(SYS_capset, &header, data)) die("capset clear failed");
}

static void verify_post_drop(const struct config *cfg) {
  gid_t groups[1];
  if (getuid()!=cfg->uid || geteuid()!=cfg->uid || getgid()!=cfg->gid || getegid()!=cfg->gid || getgroups(1, groups)!=0) die("identity drop incomplete");
  FILE *f=fopen("/proc/self/status","re"); char line[256]; int seen=0;
  if(!f) die("status unavailable");
  while(fgets(line,sizeof(line),f)) {
    if ((!strncmp(line,"CapInh:\t0000000000000000",24) || !strncmp(line,"CapPrm:\t0000000000000000",24) || !strncmp(line,"CapEff:\t0000000000000000",24) || !strncmp(line,"CapBnd:\t0000000000000000",24) || !strncmp(line,"CapAmb:\t0000000000000000",24))) seen++;
    if (!strncmp(line,"NoNewPrivs:\t1",13)) seen++;
  }
  fclose(f); if(seen!=6) die("post-drop capability state invalid");
}

static void verify_fixed_cgroup(const struct config *cfg) {
  FILE *cg=fopen("/proc/self/cgroup","re"); char actual[4096];
  if(!cg || !fgets(actual,sizeof(actual),cg)) die("cgroup unavailable");
  fclose(cg); actual[strcspn(actual,"\n")]=0;
  size_t prefix=strlen(cfg->cgroup);
  const char *instance=actual+prefix, *suffix=strstr(instance,".service");
  if(strncmp(actual,cfg->cgroup,prefix) || !*instance || !suffix || suffix[8] || suffix==instance || strchr(instance,'/') || strstr(instance,"..")) die("cgroup identity mismatch");
  for(const char *p=instance;p<suffix;p++) if(!((*p>='a'&&*p<='z')||(*p>='A'&&*p<='Z')||(*p>='0'&&*p<='9')||strchr("_.:-\\x",*p))) die("cgroup instance grammar invalid");
}

static void verify_prepared_request(const struct config *cfg) {
  unsigned char prefix[68]; ssize_t n=recv(STDIN_FILENO,prefix,sizeof(prefix),MSG_PEEK|MSG_WAITALL);
  if(n!=(ssize_t)sizeof(prefix) || memcmp(prefix,"TGWNIXO1",8)) die("prepared request prefix unavailable");
  char digest[72]; strcpy(digest,"sha256:");
  for(size_t i=0;i<32;i++) snprintf(digest+7+i*2,72-(7+i*2),"%02x",prefix[36+i]);
  digest[71]=0; if(strcmp(digest,cfg->request_sha256)) die("prepared request identity mismatch");
}

static int pin_fd(const char *path, const char *expected, int target) {
  int source=held_regular(path); char digest[72]; sha256_fd(source,digest);
  if(strcmp(digest,expected) || dup3(source,target,0)<0) die("tool pinning failed");
  close(source); return target;
}

int main(int argc, char **argv) {
  if (argc != 1 || argv[1]) die("arguments forbidden");
  struct config cfg = {0}; parse_descriptor(&cfg);
  int selffd=open("/proc/self/exe",O_RDONLY|O_CLOEXEC); struct stat selfstat;
  if(selffd<0 || fstat(selffd,&selfstat) || !S_ISREG(selfstat.st_mode) || selfstat.st_uid!=0 || (selfstat.st_mode&022)) die("launcher inode invalid");
  char digest[72]; sha256_fd(selffd,digest); close(selffd);
  if(strcmp(digest,cfg.launcher_sha256)) die("launcher digest mismatch");
  int pythonfd=pin_fd(cfg.python,cfg.python_sha256,200), ipfd=pin_fd(cfg.ip,cfg.ip_sha256,201), observerfd=pin_fd(cfg.observer,cfg.observer_sha256,202);
  pin_fd(cfg.nix,cfg.nix_sha256,203); pin_fd(cfg.nix_store,cfg.nix_store_sha256,204); pin_fd(cfg.git,cfg.git_sha256,205);
  verify_fixed_cgroup(&cfg);
  verify_prepared_request(&cfg);
  if (unshare(CLONE_NEWNET)) die("CLONE_NEWNET failed");
  char state[32]; FILE *lo=fopen("/sys/class/net/lo/operstate","re");
  if(!lo || !fgets(state,sizeof(state),lo) || strcmp(state,"down\n")) die("loopback not down");
  fclose(lo);
  DIR *net=opendir("/sys/class/net"); struct dirent *entry; int links=0;
  if(!net) die("network link inventory unavailable");
  while((entry=readdir(net))) if(strcmp(entry->d_name,".") && strcmp(entry->d_name,"..")) { if(strcmp(entry->d_name,"lo")) die("unexpected network link"); links++; }
  closedir(net); if(links!=1) die("loopback inventory invalid");
  char *route_args[]={cfg.ip,"route","show",NULL}; require_empty_ip_output(ipfd,route_args);
  char *route6_args[]={cfg.ip,"-6","route","show",NULL}; require_empty_ip_output(ipfd,route6_args);
  if (setgroups(0,NULL)) die("setgroups failed");
  unsigned secure=SECBIT_NOROOT|SECBIT_NOROOT_LOCKED|SECBIT_NO_SETUID_FIXUP|SECBIT_NO_SETUID_FIXUP_LOCKED;
  if(prctl(PR_SET_SECUREBITS,secure,0,0,0)) die("securebits failed");
  if(setresgid(cfg.gid,cfg.gid,cfg.gid)||setresuid(cfg.uid,cfg.uid,cfg.uid)) die("setresid failed");
  clear_capabilities();
  if(prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0)) die("no_new_privs failed");
  verify_post_drop(&cfg);
  char python[64], observer[64]; snprintf(python,sizeof(python),"/proc/self/fd/%d",pythonfd); snprintf(observer,sizeof(observer),"/proc/self/fd/%d",observerfd);
  char *args[]={python,"-I",observer,NULL};
  char descriptor_env[160], rule_env[160];
  snprintf(descriptor_env,sizeof(descriptor_env),"TGW_OBSERVER_DESCRIPTOR_SHA256=%s",cfg.descriptor_sha256);
  snprintf(rule_env,sizeof(rule_env),"TGW_OBSERVER_TRANSPORT_CONFIG_SHA256=%s",cfg.transport_config_sha256);
  char *env[]={"HOME=/var/empty","NIX_REMOTE=local","PATH=/run/current-system/sw/bin",descriptor_env,rule_env,NULL};
  execve(python,args,env); die("observer exec failed");
}
#endif
