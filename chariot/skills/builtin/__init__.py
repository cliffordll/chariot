"""Builtin skill manifests(YAML)— 由 `SkillLoader.load_builtin()` 通过
`importlib.resources` 扫盘加载;本 `__init__.py` 只为让 Python 把目录当成包(
让 `importlib.resources.files("chariot.skills.builtin")` 能拿到 Traversable)。
"""
