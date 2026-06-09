# PyInstaller runtime hook: fix tiktoken encoding discovery in frozen builds.
#
# tiktoken discovers encodings by iterating the `tiktoken_ext` namespace package
# via pkgutil.iter_modules(tiktoken_ext.__path__). Under PyInstaller the frozen
# importer does not enumerate namespace-package submodules, so discovery returns
# an empty list and the first get_encoding("cl100k_base") call fails with:
#
#   Unknown encoding cl100k_base. Plugins found: []
#
# The bundle scripts already ship tiktoken_ext.openai_public via --hidden-import,
# so the module is importable inside the frozen binary -- only the namespace scan
# is broken. We bypass it by pinning the known plugin module(s) directly. The
# openai_public plugin defines every standard encoding (cl100k_base, o200k_base,
# p50k_base, r50k_base, gpt2), which is everything litellm's token counter needs.
try:
    import tiktoken.registry as _reg

    _PLUGINS = ("tiktoken_ext.openai_public",)

    def _available_plugin_modules():
        return list(_PLUGINS)

    # _available_plugin_modules is functools.lru_cache-wrapped upstream; replacing
    # the attribute is safe because the registry resolves it by name at call time
    # and discovery has not run yet when runtime hooks execute.
    _reg._available_plugin_modules = _available_plugin_modules
except Exception:
    # Never let the hook crash the sidecar; if tiktoken's internals change, fall
    # back to the (possibly broken) default discovery rather than failing to boot.
    pass
