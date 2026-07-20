import os
import time
os.environ['ntwork_LOG'] = "ERROR"
import ntwork

# 本机可能同时装了多个企微目录，ntwork 自动探测常会拿到 4.1.x/5.x，
# 而实际运行/关于页仍是 4.0.8.6027，导致找不到 helper 报 WeWorkVersionNotMatchError。
# 这里强制指定与客户端一致的版本（也可在 config.json 配 wework_version）。
try:
    from config import conf
    _wework_version = conf().get("wework_version") or "4.0.8.6027"
    _wework_exe_path = conf().get("wework_exe_path") or None
except Exception:
    _wework_version = "4.0.8.6027"
    _wework_exe_path = None
ntwork.set_wework_exe_path(_wework_exe_path, _wework_version)

wework = ntwork.WeWork()


def forever():
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        ntwork.exit_()
        os._exit(0)


