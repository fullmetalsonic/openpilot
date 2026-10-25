# distutils: language = c++
# cython: language_level = 3
import builtins
import datetime
import json
from libcpp cimport bool
from libcpp.map cimport map
from libcpp.optional cimport optional
from libcpp.string cimport string
from libcpp.vector cimport vector

from openpilot.common.swaglog import cloudlog

cdef extern from "common/params.h":
  cpdef enum ParamKeyFlag:
    PERSISTENT
    CLEAR_ON_MANAGER_START
    CLEAR_ON_ONROAD_TRANSITION
    CLEAR_ON_OFFROAD_TRANSITION
    DEVELOPMENT_ONLY
    CLEAR_ON_IGNITION_ON
    ALL

  cpdef enum ParamKeyType:
    STRING
    BOOL
    INT
    FLOAT
    TIME
    JSON
    BYTES

  cdef enum ParamsCasState "ParamsCasState":
    CAS_STORED "ParamsCasState::STORED"
    CAS_CONFLICT "ParamsCasState::CONFLICT"
    CAS_FAILED "ParamsCasState::FAILED"
    CAS_UNCERTAIN "ParamsCasState::UNCERTAIN"

  cdef struct ParamsCasResult:
    ParamsCasState state
    int error
    bool changed

  cdef struct ParamsRawSnapshot:
    int error
    map[string, optional[string]] values

  cdef cppclass c_Params "Params":
    c_Params(string) except + nogil
    string get(string, bool) nogil
    bool getBool(string, bool) nogil
    int getInt(string, bool) nogil
    float getFloat(string, bool) nogil
    int remove(string) nogil
    int put(string, string) nogil
    void putNonBlocking(string, string) nogil
    void putBoolNonBlocking(string, bool) nogil
    void putIntNonBlocking(string, int) nogil
    void putFloatNonBlocking(string, float) nogil
    int putBool(string, bool) nogil
    int putInt(string, int) nogil
    int putFloat(string, float) nogil
    bool checkKey(string) nogil
    ParamKeyType getKeyType(string) nogil
    optional[string] getKeyDefaultValue(string) nogil
    string getParamPath(string) nogil
    void clearAll(ParamKeyFlag)
    vector[string] allKeys()
    ParamsRawSnapshot getManyRaw(vector[string]) nogil
    ParamsCasResult compareAndSetRaw(string, optional[string], map[string, optional[string]]) nogil

PYTHON_2_CPP = {
  (str, STRING): lambda v: v,
  (builtins.bool, BOOL): lambda v: "1" if v else "0",
  (int, INT): str,
  (float, FLOAT): str,
  (datetime.datetime, TIME): lambda v: v.isoformat(),
  (dict, JSON): json.dumps,
  (list, JSON): json.dumps,
  (bytes, BYTES): lambda v: v,
}
CPP_2_PYTHON = {
  STRING: lambda v: v.decode("utf-8"),
  BOOL: lambda v: v == b"1",
  INT: int,
  FLOAT: float,
  TIME: lambda v: datetime.datetime.fromisoformat(v.decode("utf-8")),
  JSON: json.loads,
  BYTES: lambda v: v,
}

def ensure_bytes(v):
  return v.encode() if isinstance(v, str) else v

class UnknownKeyName(Exception):
  pass

cdef class Params:
  cdef c_Params* p
  cdef str d

  def __cinit__(self, d=""):
    cdef string path = <string>d.encode()
    with nogil:
      self.p = new c_Params(path)
    self.d = d

  def __reduce__(self):
    return (type(self), (self.d,))

  def __dealloc__(self):
    del self.p

  def clear_all(self, tx_flag=ParamKeyFlag.ALL):
    self.p.clearAll(tx_flag)

  def check_key(self, key):
    key = ensure_bytes(key)
    if not self.p.checkKey(key):
      raise UnknownKeyName(key)
    return key

  def python2cpp(self, proposed_type, expected_type, value, key):
    cast = PYTHON_2_CPP.get((proposed_type, expected_type))
    if cast:
      return cast(value)
    raise TypeError(f"Type mismatch while writing param {key}: {proposed_type=} {expected_type=} {value=}")

  def _cpp2python(self, t, value, default, key):
    if value is None:
      return None
    try:
      return CPP_2_PYTHON[t](value)
    except (KeyError, TypeError, ValueError):
      cloudlog.warning(f"Failed to cast param {key} with {value=} from type {t=}")
      return self._cpp2python(t, default, None, key)

  def get(self, key, bool block=False, bool return_default=False):
    cdef string k = self.check_key(key)
    cdef ParamKeyType t = self.p.getKeyType(k)
    cdef optional[string] default = self.p.getKeyDefaultValue(k)
    cdef string val
    with nogil:
      val = self.p.get(k, block)

    default_val = (default.value() if default.has_value() else None) if return_default else None
    if val == b"":
      if block:
        # If we got no value while running in blocked mode
        # it means we got an interrupt while waiting
        raise KeyboardInterrupt
      else:
        return self._cpp2python(t, default_val, None, key)
    return self._cpp2python(t, val, default_val, key)

  def get_bool(self, key, bool block=False):
    cdef string k = self.check_key(key)
    cdef bool r
    with nogil:
      r = self.p.getBool(k, block)
    return r

  def _put_cast(self, key, dat):
    cdef string k = self.check_key(key)
    cdef ParamKeyType t = self.p.getKeyType(k)
    return ensure_bytes(self.python2cpp(type(dat), t, dat, key))

  def get_int(self, key, bool block=False):
    cdef string k = self.check_key(key)
    cdef int r
    with nogil:
      r = self.p.getInt(k, block)
    return r

  def get_float(self, key, bool block=False):
    cdef string k = self.check_key(key)
    cdef float r
    with nogil:
      r = self.p.getFloat(k, block)
    return r

  def put(self, key, dat):
    """
    Warning: This function blocks until the param is written to disk!
    In very rare cases this can take over a second, and your code will hang.
    Use the put_nonblocking, put_bool_nonblocking in time sensitive code, but
    in general try to avoid writing params as much as possible.
    """
    cdef string k = self.check_key(key)
    cdef string dat_bytes = self._put_cast(key, dat)
    with nogil:
      self.p.put(k, dat_bytes)

  def put_bool(self, key, bool val):
    cdef string k = self.check_key(key)
    with nogil:
      self.p.putBool(k, val)

  def put_int(self, key, int val):
    cdef string k = self.check_key(key)
    with nogil:
      self.p.putInt(k, val)

  def put_float(self, key, float val):
    cdef string k = self.check_key(key)
    with nogil:
      self.p.putFloat(k, val)

  def put_nonblocking(self, key, dat):
    cdef string k = self.check_key(key)
    cdef string dat_bytes = self._put_cast(key, dat)
    with nogil:
      self.p.putNonBlocking(k, dat_bytes)

  def put_bool_nonblocking(self, key, bool val):
    cdef string k = self.check_key(key)
    with nogil:
      self.p.putBoolNonBlocking(k, val)

  def put_int_nonblocking(self, key, int val):
    cdef string k = self.check_key(key)
    with nogil:
      self.p.putIntNonBlocking(k, val)

  def put_float_nonblocking(self, key, float val):
    cdef string k = self.check_key(key)
    with nogil:
      self.p.putFloatNonBlocking(k, val)

  def remove(self, key):
    cdef string k = self.check_key(key)
    with nogil:
      self.p.remove(k)

  def get_param_path(self, key=""):
    cdef string key_bytes = ensure_bytes(key)
    return self.p.getParamPath(key_bytes).decode("utf-8")

  def get_type(self, key):
    return self.p.getKeyType(self.check_key(key))

  def all_keys(self):
    return self.p.allKeys()

  def get_default_value(self, key):
    cdef string k = self.check_key(key)
    cdef ParamKeyType t = self.p.getKeyType(k)
    cdef optional[string] default = self.p.getKeyDefaultValue(k)
    return self._cpp2python(t, default.value(), None, key) if default.has_value() else None

  def cpp2python(self, key, value):
    cdef string k = self.check_key(key)
    cdef ParamKeyType t = self.p.getKeyType(k)
    return self._cpp2python(t, value, None, key)

  def get_many_raw(self, keys):
    """
    Atomically get raw binary values for multiple keys under a single lock acquisition.
    Returns a dict mapping key -> bytes | None.
    Raises OSError on any read failure without returning partial values.

    Note: This API does not add ABA/version detection, journal atomicity, or consumer ACK.
    """
    if not isinstance(keys, (list, tuple)):
      raise TypeError(f"keys must be list or tuple, got {type(keys).__name__}")

    cdef vector[string] cpp_keys
    cdef string k_bytes
    for k in keys:
      if not isinstance(k, str):
        raise TypeError(f"key must be str, got {type(k).__name__}")
      if '\0' in k:
        raise UnknownKeyName(k)
      k_bytes = k.encode('utf-8')
      if not self.p.checkKey(k_bytes):
        raise UnknownKeyName(k)
      cpp_keys.push_back(k_bytes)

    cdef ParamsRawSnapshot snapshot
    with nogil:
      snapshot = self.p.getManyRaw(cpp_keys)

    if snapshot.error != 0:
      raise OSError(snapshot.error, f"Failed to get raw params: errno={snapshot.error}")

    result = {}
    cdef optional[string] opt_val
    cdef string s
    for k in keys:
      k_bytes = k.encode('utf-8')
      opt_val = snapshot.values[k_bytes]
      if opt_val.has_value():
        s = opt_val.value()
        result[k] = s.data()[:s.size()]
      else:
        result[k] = None
    return result

  def get_raw(self, key):
    """
    Get raw binary value for a single key without type conversions or defaults.
    Returns None if missing, b"" if empty file, or raw bytes.

    Note: This API does not add ABA/version detection, journal atomicity, or consumer ACK.
    """
    if not isinstance(key, str):
      raise TypeError(f"key must be str, got {type(key).__name__}")
    return self.get_many_raw([key])[key]

  def compare_and_set_raw(self, key, value, expected):
    """
    Compare and set raw parameter value atomically under a single lock.
    Verifies that all keys in `expected` match their current disk state before mutating `key`.
    `value=None` performs conditional deletion.

    Returns dict:
      {"state": "stored" | "conflict" | "failed" | "uncertain", "errno": int, "changed": bool}

    Note: This API does not add ABA/version detection, journal atomicity, or consumer ACK.
    """
    if not isinstance(key, str):
      raise TypeError(f"key must be str, got {type(key).__name__}")
    if '\0' in key:
      raise UnknownKeyName(key)
    cdef string cpp_key = key.encode('utf-8')
    if not self.p.checkKey(cpp_key):
      raise UnknownKeyName(key)

    if not isinstance(expected, dict):
      raise TypeError(f"expected must be dict, got {type(expected).__name__}")
    if key not in expected:
      raise ValueError(f"expected must include target key '{key}'")

    if value is not None and not isinstance(value, bytes):
      raise TypeError(f"value must be bytes or None, got {type(value).__name__}")

    cdef optional[string] cpp_value
    cdef string cpp_val_str
    if value is None:
      cpp_value = optional[string]()
    else:
      cpp_val_str = <string><bytes>bytes(value)
      cpp_value = optional[string](cpp_val_str)

    cdef map[string, optional[string]] cpp_expected
    cdef string exp_k_bytes
    cdef string exp_v_bytes
    for exp_k, exp_v in expected.items():
      if not isinstance(exp_k, str):
        raise TypeError(f"expected key must be str, got {type(exp_k).__name__}")
      if '\0' in exp_k:
        raise UnknownKeyName(exp_k)
      exp_k_bytes = exp_k.encode('utf-8')
      if not self.p.checkKey(exp_k_bytes):
        raise UnknownKeyName(exp_k)

      if exp_v is None:
        cpp_expected[exp_k_bytes] = optional[string]()
      elif isinstance(exp_v, bytes):
        exp_v_bytes = <string><bytes>bytes(exp_v)
        cpp_expected[exp_k_bytes] = optional[string](exp_v_bytes)
      else:
        raise TypeError(f"expected value for key '{exp_k}' must be bytes or None, got {type(exp_v).__name__}")

    cdef ParamsCasResult res
    with nogil:
      res = self.p.compareAndSetRaw(cpp_key, cpp_value, cpp_expected)

    cdef str state_str
    if res.state == CAS_STORED:
      state_str = "stored"
    elif res.state == CAS_CONFLICT:
      state_str = "conflict"
    elif res.state == CAS_FAILED:
      state_str = "failed"
    else:
      state_str = "uncertain"

    return {
      "state": state_str,
      "errno": res.error,
      "changed": res.changed,
    }
