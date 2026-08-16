/* Static, no-argument W09 controller launcher.
 *
 * The launcher reads one fixed root-protected binding, opens the admitted
 * Python executable and source-only controller zip through protected ancestor
 * chains, retains its own executable and the binding, and passes those exact
 * descriptors through fexecve.  Python never needs to reopen an execution
 * input by pathname.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef BINDING_PATH
#define BINDING_PATH "/etc/tgw/w09/application-bootstrap-runtime.fds"
#endif
#ifndef TGW_TRUSTED_UID
#define TGW_TRUSTED_UID 0
#endif
#define MAX_BINDING_BYTES 8192
#define MAX_CLOSURE_BYTES (4 * 1024 * 1024)
#define MAX_RUNTIME_FILES 2048
#define RUNTIME_FD_BASE 1000

static int protected_metadata(const struct stat *value, int executable) {
    if (!S_ISREG(value->st_mode) ||
        (value->st_uid != 0 && value->st_uid != TGW_TRUSTED_UID) ||
        value->st_nlink < 1 ||
        (value->st_mode & 0022) != 0) {
        return 0;
    }
    return !executable || (value->st_mode & 0111) != 0;
}

static int protected_directory(const struct stat *value) {
    return S_ISDIR(value->st_mode) &&
        (value->st_uid == 0 || value->st_uid == TGW_TRUSTED_UID) &&
        (value->st_mode & 0022) == 0;
}

static int open_protected(const char *path, int executable, int directory_leaf) {
    char copy[PATH_MAX];
    char *component;
    char *save = NULL;
    int directory = -1;
    int child = -1;
    struct stat value;

    if (path == NULL || path[0] != '/' || strlen(path) >= sizeof(copy)) {
        errno = EINVAL;
        return -1;
    }
    memcpy(copy, path + 1, strlen(path));
    directory = open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (directory < 0) {
        return -1;
    }
    component = strtok_r(copy, "/", &save);
    while (component != NULL) {
        char *next = strtok_r(NULL, "/", &save);
        if (component[0] == '\0' || strcmp(component, ".") == 0 ||
            strcmp(component, "..") == 0) {
            errno = EINVAL;
            goto fail;
        }
        if (next == NULL) {
            int flags = O_RDONLY | O_NOFOLLOW | O_CLOEXEC;
            if (directory_leaf) {
                flags |= O_DIRECTORY;
            }
            child = openat(directory, component, flags);
            if (child < 0 || fstat(child, &value) != 0 ||
                (directory_leaf ? !protected_directory(&value)
                                : !protected_metadata(&value, executable))) {
                goto fail;
            }
            close(directory);
            return child;
        }
        child = openat(
            directory,
            component,
            O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
        );
        if (child < 0 || fstat(child, &value) != 0 ||
            !protected_directory(&value)) {
            goto fail;
        }
        close(directory);
        directory = child;
        child = -1;
        component = next;
    }
    errno = EINVAL;
fail:
    if (child >= 0) {
        close(child);
    }
    close(directory);
    return -1;
}

static int retain_fd(int fd) {
    int flags = fcntl(fd, F_GETFD);
    return flags >= 0 && fcntl(fd, F_SETFD, flags & ~FD_CLOEXEC) == 0;
}

static int read_binding(int fd, char *raw, size_t capacity) {
    size_t offset = 0;
    for (;;) {
        ssize_t count = read(fd, raw + offset, capacity - offset);
        if (count < 0) {
            if (errno == EINTR) {
                continue;
            }
            return 0;
        }
        if (count == 0) {
            break;
        }
        offset += (size_t)count;
        if (offset == capacity) {
            return 0;
        }
    }
    raw[offset] = '\0';
    return 1;
}

static int parse_binding(
    char *raw,
    char **python,
    char **bundle,
    char **closure,
    char **receipt
) {
    static const char prefix[] =
        "schema=tgw-w09-controller-launch-fds/v1\npython=";
    char *separator;
    char *second;
    char *third;
    char *end;
    if (strncmp(raw, prefix, sizeof(prefix) - 1) != 0) {
        return 0;
    }
    *python = raw + sizeof(prefix) - 1;
    separator = strchr(*python, '\n');
    if (separator == NULL || strncmp(separator + 1, "bundle=", 7) != 0) {
        return 0;
    }
    *separator = '\0';
    *bundle = separator + 8;
    second = strchr(*bundle, '\n');
    if (second == NULL || strncmp(second + 1, "closure=", 8) != 0) {
        return 0;
    }
    *second = '\0';
    *closure = second + 9;
    third = strchr(*closure, '\n');
    if (third == NULL || strncmp(third + 1, "receipt=", 8) != 0) {
        return 0;
    }
    *third = '\0';
    *receipt = third + 9;
    end = strchr(*receipt, '\n');
    if (end == NULL || end[1] != '\0') {
        return 0;
    }
    *end = '\0';
    return (*python)[0] == '/' && (*bundle)[0] == '/' && (*closure)[0] == '/' &&
        (*receipt)[0] == '/';
}

static int parse_unsigned(const char *text, unsigned long long *value) {
    char *end = NULL;
    errno = 0;
    *value = strtoull(text, &end, 10);
    return errno == 0 && end != text && *end == '\0';
}

static int hold_runtime_closure(int closure_fd) {
    static char raw[MAX_CLOSURE_BYTES + 1];
    char *line;
    char *save_line = NULL;
    int count = 0;

    if (lseek(closure_fd, 0, SEEK_SET) < 0 ||
        !read_binding(closure_fd, raw, MAX_CLOSURE_BYTES)) {
        return -1;
    }
    line = strtok_r(raw, "\n", &save_line);
    if (line == NULL ||
        strcmp(line, "schema=tgw-w09-controller-preexec-closure/v1") != 0) {
        return -1;
    }
    while ((line = strtok_r(NULL, "\n", &save_line)) != NULL) {
        char *fields[8];
        char *cursor;
        char *save_field = NULL;
        unsigned long long expected[7];
        struct stat observed;
        int source_fd;
        int target_fd;

        int directory_leaf = strncmp(line, "tree=", 5) == 0;
        if (count >= MAX_RUNTIME_FILES ||
            (!directory_leaf && strncmp(line, "file=", 5) != 0)) {
            return -1;
        }
        cursor = line + 5;
        for (int index = 0; index < 8; ++index) {
            fields[index] = strtok_r(index == 0 ? cursor : NULL, ":", &save_field);
            if (fields[index] == NULL) {
                return -1;
            }
        }
        if (strtok_r(NULL, ":", &save_field) != NULL || fields[7][0] != '/') {
            return -1;
        }
        for (int index = 0; index < 7; ++index) {
            if (!parse_unsigned(fields[index], &expected[index])) {
                return -1;
            }
        }
        source_fd = open_protected(
            fields[7],
            !directory_leaf && (expected[4] & 0111) != 0,
            directory_leaf
        );
        if (source_fd < 0 || fstat(source_fd, &observed) != 0 ||
            (unsigned long long)observed.st_dev != expected[0] ||
            (unsigned long long)observed.st_ino != expected[1] ||
            (unsigned long long)observed.st_uid != expected[2] ||
            (unsigned long long)observed.st_gid != expected[3] ||
            (unsigned long long)observed.st_mode != expected[4] ||
            (unsigned long long)observed.st_nlink != expected[5] ||
            (unsigned long long)observed.st_size != expected[6]) {
            return -1;
        }
        target_fd = RUNTIME_FD_BASE + count;
        if (source_fd == target_fd) {
            source_fd = fcntl(source_fd, F_DUPFD_CLOEXEC, RUNTIME_FD_BASE + MAX_RUNTIME_FILES);
            if (source_fd < 0) {
                return -1;
            }
        }
        if (dup2(source_fd, target_fd) < 0 || !retain_fd(target_fd)) {
            close(source_fd);
            return -1;
        }
        close(source_fd);
        ++count;
    }
    return count > 0 ? count : -1;
}

int main(int argc, char **argv) {
    char binding[MAX_BINDING_BYTES + 1];
    char bundle_argument[64];
    char launcher_environment[64];
    char python_environment[64];
    char bundle_environment[64];
    char binding_environment[64];
    char closure_environment[64];
    char runtime_base_environment[64];
    char runtime_count_environment[64];
    char receipt_environment[64];
#ifdef TGW_RUNTIME_IMPORT_PROBE
    char probe_environment[] = "TGW_W09_RUNTIME_PROBE=1";
#endif
    char *python_path = NULL;
    char *bundle_path = NULL;
    char *closure_path = NULL;
    char *receipt_path = NULL;
    int binding_fd;
    int launcher_fd;
    int python_fd;
    int bundle_fd;
    int closure_fd;
    int runtime_count;
    int receipt_fd;
    struct stat launcher_metadata;

    (void)argv;
    if (argc != 1) {
        return 64;
    }
    binding_fd = open_protected(BINDING_PATH, 0, 0);
    launcher_fd = open("/proc/self/exe", O_RDONLY | O_CLOEXEC);
    if (binding_fd < 0 || launcher_fd < 0 ||
        fstat(launcher_fd, &launcher_metadata) != 0 ||
        !protected_metadata(&launcher_metadata, 1) ||
        !read_binding(binding_fd, binding, MAX_BINDING_BYTES) ||
        !parse_binding(
            binding,
            &python_path,
            &bundle_path,
            &closure_path,
            &receipt_path
        )) {
        return 65;
    }
    python_fd = open_protected(python_path, 1, 0);
    bundle_fd = open_protected(bundle_path, 0, 0);
    closure_fd = open_protected(closure_path, 0, 0);
    receipt_fd = open_protected(receipt_path, 0, 0);
    runtime_count = closure_fd < 0 ? -1 : hold_runtime_closure(closure_fd);
    if (python_fd < 0 || bundle_fd < 0 || closure_fd < 0 || receipt_fd < 0 ||
        runtime_count < 1 ||
        !retain_fd(binding_fd) || !retain_fd(launcher_fd) ||
        !retain_fd(python_fd) || !retain_fd(bundle_fd) || !retain_fd(closure_fd) ||
        !retain_fd(receipt_fd)) {
        return 66;
    }
    if (snprintf(bundle_argument, sizeof(bundle_argument), "/proc/self/fd/%d", bundle_fd) < 0 ||
        snprintf(launcher_environment, sizeof(launcher_environment), "TGW_W09_LAUNCHER_FD=%d", launcher_fd) < 0 ||
        snprintf(python_environment, sizeof(python_environment), "TGW_W09_PYTHON_FD=%d", python_fd) < 0 ||
        snprintf(bundle_environment, sizeof(bundle_environment), "TGW_W09_BUNDLE_FD=%d", bundle_fd) < 0 ||
        snprintf(binding_environment, sizeof(binding_environment), "TGW_W09_LAUNCH_BINDING_FD=%d", binding_fd) < 0 ||
        snprintf(closure_environment, sizeof(closure_environment), "TGW_W09_CLOSURE_FD=%d", closure_fd) < 0 ||
        snprintf(runtime_base_environment, sizeof(runtime_base_environment), "TGW_W09_RUNTIME_FD_BASE=%d", RUNTIME_FD_BASE) < 0 ||
        snprintf(runtime_count_environment, sizeof(runtime_count_environment), "TGW_W09_RUNTIME_FD_COUNT=%d", runtime_count) < 0 ||
        snprintf(receipt_environment, sizeof(receipt_environment), "TGW_W09_RUNTIME_RECEIPT_FD=%d", receipt_fd) < 0) {
        return 67;
    }
    char *const python_argv[] = {
        (char *)"python3",
        (char *)"-I",
        (char *)"-B",
        (char *)"-X",
        (char *)"pycache_prefix=/proc/self/fd/2147483647",
        (char *)"-S",
        bundle_argument,
        NULL,
    };
    char *const clean_environment[] = {
        (char *)"LANG=C",
        (char *)"LC_ALL=C",
        (char *)"PATH=",
        (char *)"PYTHONDONTWRITEBYTECODE=1",
        launcher_environment,
        python_environment,
        bundle_environment,
        binding_environment,
        closure_environment,
        runtime_base_environment,
        runtime_count_environment,
        receipt_environment,
#ifdef TGW_RUNTIME_IMPORT_PROBE
        probe_environment,
#endif
        NULL,
    };
    fexecve(python_fd, python_argv, clean_environment);
    return errno == 0 ? 70 : errno;
}
