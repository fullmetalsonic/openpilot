#include "common/params.h"

#include <dirent.h>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cassert>
#include <csignal>
#include <unordered_map>

#include "common/params_keys.h"
#include "common/queue.h"
#include "common/swaglog.h"
#include "common/util.h"
#include "system/hardware/hw.h"

namespace {

volatile sig_atomic_t params_do_exit = 0;
void params_sig_handler(int signal) {
  params_do_exit = 1;
}

int fsync_dir(const std::string &path) {
  int result = -1;
  int fd = HANDLE_EINTR(open(path.c_str(), O_RDONLY, 0755));
  if (fd >= 0) {
    result = HANDLE_EINTR(fsync(fd));
    int err = errno;
    HANDLE_EINTR(close(fd));
    errno = err;
  }
  return result;
}

bool create_params_path(const std::string &param_path, const std::string &key_path) {
  // Make sure params path exists
  if (!util::file_exists(param_path) && !util::create_directories(param_path, 0775)) {
    return false;
  }

  // See if the symlink exists, otherwise create it
  if (!util::file_exists(key_path)) {
    // 1) Create temp folder
    // 2) Symlink it to temp link
    // 3) Move symlink to <params>/d

    std::string tmp_path = param_path + "/.tmp_XXXXXX";
    // this should be OK since mkdtemp just replaces characters in place
    char *tmp_dir = mkdtemp((char *)tmp_path.c_str());
    if (tmp_dir == NULL) {
      return false;
    }

    std::string link_path = std::string(tmp_dir) + ".link";
    if (symlink(tmp_dir, link_path.c_str()) != 0) {
      return false;
    }

    // don't return false if it has been created by other
    if (rename(link_path.c_str(), key_path.c_str()) != 0 && errno != EEXIST) {
      return false;
    }
  }

  return true;
}

std::string ensure_params_path(const std::string &prefix, const std::string &path = {}) {
  std::string params_path = path.empty() ? Path::params() : path;
  if (!create_params_path(params_path, params_path + prefix)) {
    throw std::runtime_error(util::string_format(
        "Failed to ensure params path, errno=%d, path=%s, param_prefix=%s",
        errno, params_path.c_str(), prefix.c_str()));
  }
  return params_path;
}

class FileLock {
public:
  FileLock(const std::string &fn) {
    fd_ = HANDLE_EINTR(open(fn.c_str(), O_CREAT, 0775));
    if (fd_ < 0) {
      err_ = errno;
      LOGE("Failed to open lock file %s, errno=%d", fn.c_str(), errno);
      return;
    }
    if (HANDLE_EINTR(flock(fd_, LOCK_EX)) < 0) {
      err_ = errno;
      LOGE("Failed to lock file %s, errno=%d", fn.c_str(), errno);
      close(fd_);
      fd_ = -1;
      return;
    }
    err_ = 0;
  }
  ~FileLock() {
    if (fd_ >= 0) {
      close(fd_);
    }
  }

  bool locked() const { return fd_ >= 0; }
  int error() const { return err_; }

private:
  int fd_ = -1;
  int err_ = 0;
};

static int read_raw_value(const std::string &path, std::optional<std::string> &val) {
  val = std::nullopt;
  // O_NONBLOCK avoids waiting for a FIFO peer before fstat can reject it.
  int fd = HANDLE_EINTR(open(path.c_str(), O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK));
  if (fd < 0) {
    if (errno == ENOENT) {
      return 0;
    }
    return errno;
  }

  struct stat st;
  if (HANDLE_EINTR(fstat(fd, &st)) < 0) {
    int err = errno;
    close(fd);
    return err;
  }

  if (!S_ISREG(st.st_mode)) {
    close(fd);
    return S_ISDIR(st.st_mode) ? EISDIR : EINVAL;
  }

  std::string content;
  if (st.st_size > 0) {
    content.reserve(st.st_size);
  }
  char buf[4096];
  while (true) {
    ssize_t bytes_read = HANDLE_EINTR(read(fd, buf, sizeof(buf)));
    if (bytes_read < 0) {
      int err = errno;
      close(fd);
      return err;
    }
    if (bytes_read == 0) {
      break;
    }
    content.append(buf, bytes_read);
  }
  // Linux close may have released the fd even on EINTR; never retry it.
  if (close(fd) < 0) return errno;
  val = content;
  return 0;
}

} // namespace


Params::Params(const std::string &path) {
  params_prefix = "/" + util::getenv("OPENPILOT_PREFIX", "d");
  params_path = ensure_params_path(params_prefix, path);
}

Params::~Params() {
  if (future.valid()) {
    future.wait();
  }
  assert(queue.empty());
}

std::vector<std::string> Params::allKeys() const {
  std::vector<std::string> ret;
  for (auto &p : keys) {
    ret.push_back(p.first);
  }
  return ret;
}

bool Params::checkKey(const std::string &key) {
  return keys.find(key) != keys.end();
}

ParamKeyFlag Params::getKeyFlag(const std::string &key) {
  return static_cast<ParamKeyFlag>(keys[key].flags);
}

ParamKeyType Params::getKeyType(const std::string &key) {
  return keys[key].type;
}

std::optional<std::string> Params::getKeyDefaultValue(const std::string &key) {
  return keys[key].default_value;
}

int Params::put(const char* key, const char* value, size_t value_size) {
  // Information about safely and atomically writing a file: https://lwn.net/Articles/457667/
  // 1) Create temp file
  // 2) Write data to temp file
  // 3) fsync() the temp file
  // 4) rename the temp file to the real name
  // 5) fsync() the containing directory
  std::string tmp_path = params_path + "/.tmp_value_XXXXXX";
  int tmp_fd = mkstemp((char*)tmp_path.c_str());
  if (tmp_fd < 0) return -1;

  int result = -1;
  do {
    // Write value to temp.
    ssize_t bytes_written = HANDLE_EINTR(write(tmp_fd, value, value_size));
    if (bytes_written < 0 || (size_t)bytes_written != value_size) {
      result = -20;
      break;
    }

    // fsync to force persist the changes.
    if ((result = HANDLE_EINTR(fsync(tmp_fd))) < 0) break;

    FileLock file_lock(params_path + "/.lock");

    // Move temp into place.
    if ((result = rename(tmp_path.c_str(), getParamPath(key).c_str())) < 0) break;

    // fsync parent directory
    result = fsync_dir(getParamPath());
  } while (false);

  close(tmp_fd);
  if (result != 0) {
    ::unlink(tmp_path.c_str());
  }
  return result;
}

int Params::remove(const std::string &key) {
  FileLock file_lock(params_path + "/.lock");
  int result = unlink(getParamPath(key).c_str());
  if (result != 0) {
    return result;
  }
  return fsync_dir(getParamPath());
}

std::string Params::get(const std::string &key, bool block) {
  if (!block) {
    return util::read_file(getParamPath(key));
  } else {
    // blocking read until successful
    params_do_exit = 0;
    void (*prev_handler_sigint)(int) = std::signal(SIGINT, params_sig_handler);
    void (*prev_handler_sigterm)(int) = std::signal(SIGTERM, params_sig_handler);

    std::string value;
    while (!params_do_exit) {
      if (value = util::read_file(getParamPath(key)); !value.empty()) {
        break;
      }
      util::sleep_for(100);  // 0.1 s
    }

    std::signal(SIGINT, prev_handler_sigint);
    std::signal(SIGTERM, prev_handler_sigterm);
    return value;
  }
}

std::map<std::string, std::string> Params::readAll() {
  FileLock file_lock(params_path + "/.lock");
  return util::read_files_in_dir(getParamPath());
}

void Params::clearAll(ParamKeyFlag key_flag) {
  FileLock file_lock(params_path + "/.lock");

  // 1) delete params of key_flag
  // 2) delete files that are not defined in the keys.
  if (DIR *d = opendir(getParamPath().c_str())) {
    struct dirent *de = NULL;
    while ((de = readdir(d))) {
      if (de->d_type != DT_DIR) {
        auto it = keys.find(de->d_name);
        if (it == keys.end() || (it->second.flags & key_flag)) {
          unlink(getParamPath(de->d_name).c_str());
        }
      }
    }
    closedir(d);
  }

  fsync_dir(getParamPath());
}

void Params::putNonBlocking(const std::string &key, const std::string &val) {
   queue.push(std::make_pair(key, val));
  // start thread on demand
  if (!future.valid() || future.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready) {
    future = std::async(std::launch::async, &Params::asyncWriteThread, this);
  }
}

void Params::asyncWriteThread() {
  // TODO: write the latest one if a key has multiple values in the queue.
  std::pair<std::string, std::string> p;
  while (queue.try_pop(p, 0)) {
    // Params::put is Thread-Safe
    put(p.first, p.second);
  }
}

ParamsRawSnapshot Params::getManyRaw(const std::vector<std::string> &keys_to_read) {
  ParamsRawSnapshot snapshot;
  snapshot.error = 0;

  for (const auto &key : keys_to_read) {
    if (!checkKey(key)) {
      snapshot.error = EINVAL;
      snapshot.values.clear();
      return snapshot;
    }
  }

  FileLock file_lock(params_path + "/.lock");
  if (!file_lock.locked()) {
    snapshot.error = file_lock.error() != 0 ? file_lock.error() : EIO;
    return snapshot;
  }

  for (const auto &key : keys_to_read) {
    std::optional<std::string> val;
    int err = read_raw_value(getParamPath(key), val);
    if (err != 0) {
      snapshot.error = err;
      snapshot.values.clear();
      return snapshot;
    }
    snapshot.values[key] = std::move(val);
  }

  return snapshot;
}

ParamsCasResult Params::compareAndSetRaw(
    const std::string &key,
    const std::optional<std::string> &value,
    const std::map<std::string, std::optional<std::string>> &expected) {

  // 1) Validate registered keys
  if (!checkKey(key)) {
    return {ParamsCasState::FAILED, EINVAL, false};
  }
  if (expected.find(key) == expected.end()) {
    return {ParamsCasState::FAILED, EINVAL, false};
  }
  for (const auto &[k, exp_val] : expected) {
    if (!checkKey(k)) {
      return {ParamsCasState::FAILED, EINVAL, false};
    }
  }

  // 2) Prepare candidate temporary file + full write + file fsync (when needed)
  std::string tmp_path;
  bool has_temp_file = false;

  if (value.has_value()) {
    tmp_path = params_path + "/.tmp_cas_XXXXXX";
    int tmp_fd = mkstemp((char*)tmp_path.c_str());
    if (tmp_fd < 0) {
      return {ParamsCasState::FAILED, errno, false};
    }
    has_temp_file = true;

    const std::string &val_str = *value;
    const char *buf = val_str.data();
    size_t remaining = val_str.size();

    while (remaining > 0) {
      ssize_t written = HANDLE_EINTR(write(tmp_fd, buf, remaining));
      if (written < 0) {
        int err = errno;
        close(tmp_fd);
        unlink(tmp_path.c_str());
        return {ParamsCasState::FAILED, err, false};
      }
      if (written == 0) {
        close(tmp_fd);
        unlink(tmp_path.c_str());
        return {ParamsCasState::FAILED, EIO, false};
      }
      buf += written;
      remaining -= written;
    }

    if (HANDLE_EINTR(fsync(tmp_fd)) < 0) {
      int err = errno;
      close(tmp_fd);
      unlink(tmp_path.c_str());
      return {ParamsCasState::FAILED, err, false};
    }

    if (close(tmp_fd) < 0) {
      int err = errno;
      unlink(tmp_path.c_str());
      return {ParamsCasState::FAILED, err, false};
    }
  }

  // 3) Acquire same existing .lock
  FileLock file_lock(params_path + "/.lock");
  if (!file_lock.locked()) {
    int err = file_lock.error() != 0 ? file_lock.error() : EIO;
    if (has_temp_file) {
      unlink(tmp_path.c_str());
    }
    return {ParamsCasState::FAILED, err, false};
  }

  // 4) Compare every expected raw state under that single lock
  std::optional<std::string> current_target_val;
  for (const auto &[k, exp_val] : expected) {
    std::optional<std::string> cur_val;
    int err = read_raw_value(getParamPath(k), cur_val);
    if (err != 0) {
      if (has_temp_file) {
        unlink(tmp_path.c_str());
      }
      return {ParamsCasState::FAILED, err, false};
    }
    if (k == key) {
      current_target_val = cur_val;
    }
    if (cur_val != exp_val) {
      if (has_temp_file) {
        unlink(tmp_path.c_str());
      }
      return {ParamsCasState::CONFLICT, 0, false};
    }
  }

  // Idempotent / no-op check: requested value already matches target disk state
  if (current_target_val == value) {
    if (has_temp_file) {
      unlink(tmp_path.c_str());
    }
    return {ParamsCasState::STORED, 0, false};
  }

  // 5) Single key rename/unlink
  if (value.has_value()) {
    if (HANDLE_EINTR(rename(tmp_path.c_str(), getParamPath(key).c_str())) < 0) {
      int err = errno;
      unlink(tmp_path.c_str());
      return {ParamsCasState::FAILED, err, false};
    }
  } else {
    if (HANDLE_EINTR(unlink(getParamPath(key).c_str())) < 0) {
      int err = errno;
      return {ParamsCasState::FAILED, err, false};
    }
  }

  // 6) Directory fsync
  if (fsync_dir(getParamPath()) < 0) {
    int err = errno != 0 ? errno : EIO;
    return {ParamsCasState::UNCERTAIN, err, true};
  }

  return {ParamsCasState::STORED, 0, true};
}
