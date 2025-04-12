import datetime
import os
import platform
import sys
from configparser import ConfigParser


def mini_conf():
    cwd = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(cwd, 'config.ini')
    dev_path = os.path.join(cwd, 'config.dev.ini')
    config = ConfigParser()
    if os.path.exists(dev_path):
        config.read(dev_path, encoding='utf-8-sig')
    else:
        config.read(path, encoding='utf-8-sig')
    return config


raw_stdout = sys.stdout


class Stdout:

    def __init__(self):
        self.log_file = mini_conf().get('dev', 'log_file', fallback='')
        if self.log_file:
            if self.log_file.startswith('./'):
                cwd = os.path.dirname(os.path.dirname(__file__))
                self.log_file = os.path.join(cwd, self.log_file.split('./', 1)[1])
            mode = 'a' if os.path.exists(self.log_file) and os.path.getsize(self.log_file) < 10 * 1024000 else 'w'
            if not os.path.exists(self.log_file):
                os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            self.log_file = open(self.log_file, mode, encoding='utf-8')

    def write(self, *args, end=''):
        log = str(*args) + end
        if MyLogger.need_mix:
            log = MyLogger.mix_args_str(log)[0]
        raw_stdout.write(log)
        if self.log_file:
            self.log_file.write(log)
            self.log_file.flush()

    def flush(self):
        pass


if mini_conf().get('dev', 'log_file', fallback=''):
    sys.stdout = Stdout()
    sys.stderr = sys.stdout


class MyLogger:
    need_mix = True
    api_key = '_hide_api_key_'
    netloc = '_mix_netloc_'
    netloc_replace = '_mix_netloc_'
    user_name = os.getlogin()

    def __init__(self):
        self.debug_mode = configs.debug_mode

    @staticmethod
    def mix_host_gen(netloc):
        host, *port = netloc.split(':')
        port = ':' + port[0] if port else ''
        new = host[:len(host) // 2] + '_mix_host_' + port
        return new

    @staticmethod
    def mix_args_str(*args):
        return [str(i).replace(MyLogger.api_key, '_hide_api_key_')
                .replace(MyLogger.netloc, MyLogger.netloc_replace)
                .replace(MyLogger.user_name, '_hide_user_')
                for i in args]

    @staticmethod
    def log(*args, end=None, silence=False):
        if silence:
            return
        t = f"[{datetime.datetime.now().strftime('%D %H:%M:%S.%f')[:19]}] "
        args = ' '.join(str(i) for i in args)
        print(t + args, end=end)

    def info(self, *args, end=None, silence=False):
        if not silence and MyLogger.need_mix:
            args = self.mix_args_str(*args)
        self.log(*args, end=end, silence=silence)

    def debug(self, *args, end=None, silence=False):
        if self.debug_mode:
            self.log(*args, end=end, silence=silence)

    def error(self, *args, end=None, silence=False):
        self.log(*args, end=end, silence=silence)

    def level(self):
        if self.debug_mode:
            return "DEBUG"
        return "INFO"


class Configs:

    def __init__(self):
        self.platform = platform.system()
        self.cwd = os.path.dirname(os.path.dirname(__file__))
        self.path = os.path.join(self.cwd, 'config.ini')
        self.dev_path = os.path.join(self.cwd, 'config.dev.ini')
        self.active_config_path = self.dev_path if os.path.exists(self.dev_path) else self.path
        self.raw: ConfigParser = self.update()
        MyLogger.log(MyLogger.mix_args_str(f'Python path: {sys.executable}'))
        MyLogger.log(MyLogger.mix_args_str(f'ini path: {self.active_config_path}'))
        MyLogger.log(f'{platform.platform(True)} Python-{platform.python_version()}')
        self.debug_mode = self.raw.getboolean('dev', 'debug', fallback=False)

    def update(self):
        config = ConfigParser()
        # 首先检查是否存在开发配置文件
        if os.path.exists(self.dev_path):
            config.read(self.dev_path, encoding='utf-8-sig')
            MyLogger.log(f'使用开发配置文件: {self.dev_path}')
            self.active_config_path = self.dev_path
        else:
            config.read(self.path, encoding='utf-8-sig')
            self.active_config_path = self.path
        return config


configs = Configs()
