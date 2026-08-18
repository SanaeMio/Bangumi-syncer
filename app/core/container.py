"""轻量「可注入单例」容器：为模块级全局单例提供惰性实例化与测试/DI 覆盖钩子。

用法（模块内）：

.. code-block:: python

    _injectable = Injectable(MyService)

    def get_my_service() -> MyService:
        return _injectable.get()

    def set_my_service(instance: MyService) -> None:
        _injectable.set(instance)

    def reset_my_service() -> None:
        _injectable.reset()

    def __getattr__(name: str):
        if name == "my_service":
            return _injectable.get()
        raise AttributeError(...)

- 模块属性名（``my_service``）保持原有 ``from ... import my_service`` 访问方式不变；
- 测试可通过 ``set_my_service(fake)`` 替换实例，比 monkeypatch 模块属性更显式，
  为后续引入 FastAPI DI 提供统一的可替换入口（消费方迁移到 ``get_my_service()``）。
"""

from typing import Any, Callable, Optional


class Injectable:
    """惰性单例持有者：首次 get() 才创建实例，set()/reset() 支持替换与复原。"""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._instance: Optional[Any] = None
        self._loaded = False

    def get(self) -> Any:
        """返回当前持有实例；未加载时惰性创建。"""
        if not self._loaded:
            self._instance = self._factory()
            self._loaded = True
        return self._instance

    def set(self, instance: Any) -> None:
        """注入替换实例（测试/DI 使用）。"""
        self._instance = instance
        self._loaded = True

    def reset(self) -> None:
        """清空持有实例，下次 get() 重新按工厂创建。"""
        self._instance = None
        self._loaded = False
