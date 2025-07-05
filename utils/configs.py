import datetime
import os
import platform
import sys
from configparser import ConfigParser


def mini_conf():
    cwd = os.path.dirname(os.path.dirname(__file__))
    
    # 支持Docker环境变量指定配置文件路径
    config_file = os.environ.get('CONFIG_FILE')
    if config_file and os.path.exists(config_file):
        path = config_file
    else:
        # 检查挂载的配置文件
        mounted_config = '/app/config/config.ini'
        if os.path.exists(mounted_config):
            path = mounted_config
        else:
            # 默认配置文件路径
            path = os.path.join(cwd, 'config.ini')
            dev_path = os.path.join(cwd, 'config.dev.ini')
            if os.path.exists(dev_path):
                path = dev_path
    
    config = ConfigParser()
    config.read(path, encoding='utf-8-sig')
    return config


# 安全地获取用户名，Docker环境下使用默认值
def get_safe_username():
    try:
        return os.getlogin()
    except (OSError, AttributeError):
        # Docker环境或其他无法获取登录用户的环境
        return os.environ.get('USER', os.environ.get('USERNAME', 'docker_user'))


raw_stdout = sys.stdout


class MyLogger:
    need_mix = True
    api_key = '_hide_api_key_'
    netloc = '_mix_netloc_'
    netloc_replace = '_mix_netloc_'
    user_name = get_safe_username()

    def __init__(self):
        # 延迟获取debug_mode，避免循环依赖
        self._debug_mode = None

    @property
    def debug_mode(self):
        # 每次都重新获取，确保配置更新后立即生效
        try:
            return mini_conf().getboolean('dev', 'debug', fallback=False)
        except:
            return False

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


class Configs:

    def __init__(self):
        self.platform = platform.system()
        self.cwd = os.path.dirname(os.path.dirname(__file__))
        
        # 支持Docker环境变量指定配置文件路径
        config_file = os.environ.get('CONFIG_FILE')
        if config_file and os.path.exists(config_file):
            self.path = config_file
            self.dev_path = config_file
            self.active_config_path = config_file
        else:
            # 检查挂载的配置文件
            mounted_config = '/app/config/config.ini'
            if os.path.exists(mounted_config):
                self.path = mounted_config
                self.dev_path = mounted_config
                self.active_config_path = mounted_config
            else:
                # 默认配置文件路径
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
        
        # 支持Docker环境变量指定配置文件路径
        config_file = os.environ.get('CONFIG_FILE')
        if config_file and os.path.exists(config_file):
            config.read(config_file, encoding='utf-8-sig')
            MyLogger.log(f'使用环境变量指定的配置文件: {config_file}')
            self.active_config_path = config_file
        else:
            # 检查挂载的配置文件
            mounted_config = '/app/config/config.ini'
            if os.path.exists(mounted_config):
                config.read(mounted_config, encoding='utf-8-sig')
                MyLogger.log(f'使用挂载的配置文件: {mounted_config}')
                self.active_config_path = mounted_config
            else:
                # 首先检查是否存在开发配置文件
                if os.path.exists(self.dev_path):
                    config.read(self.dev_path, encoding='utf-8-sig')
                    MyLogger.log(f'使用开发配置文件: {self.dev_path}')
                    self.active_config_path = self.dev_path
                else:
                    config.read(self.path, encoding='utf-8-sig')
                    self.active_config_path = self.path
        
        # 支持环境变量覆盖关键配置项
        env_overrides = {
            ('bangumi', 'username'): 'BANGUMI_USERNAME',
            ('bangumi', 'access_token'): 'BANGUMI_ACCESS_TOKEN', 
            ('sync', 'single_username'): 'SINGLE_USERNAME',
            ('bangumi', 'private'): 'BANGUMI_PRIVATE',
            ('dev', 'script_proxy'): 'HTTP_PROXY',
            ('dev', 'debug'): 'DEBUG_MODE'
        }
        
        for (section, option), env_var in env_overrides.items():
            env_value = os.environ.get(env_var)
            if env_value:
                if not config.has_section(section):
                    config.add_section(section)
                config.set(section, option, env_value)
                MyLogger.log(f'环境变量覆盖配置: {section}.{option} = {env_var}')
        
        # 更新self.raw属性
        self.raw = config
        return config


configs = Configs()
