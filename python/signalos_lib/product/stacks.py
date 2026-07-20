"""Profile-aware stack adapter contract and implementations.

Each adapter knows how to detect, scaffold, resolve targets, plan
validation commands, and plan preview/dev-server configuration for a
particular product stack.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

@runtime_checkable
class StackAdapter(Protocol):
    """Contract for profile-aware stack adapters."""

    id: str
    display_name: str

    def detect(self, repo_root: Path) -> dict[str, Any]:
        """Detect stack characteristics from an existing repo.

        Returns detected info dict including at minimum:
        - ``can_deliver_ui``
        - ``can_deliver_runnable``
        """
        ...

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        """Create governance metadata for this stack.

        Returns manifest of scaffold specification.  Must include
        ``can_deliver_ui`` and ``can_deliver_runnable`` flags.
        """
        ...

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        """Resolve target paths for source, tests, config.

        Returns path mapping with keys like ``source``, ``tests``,
        ``config``, ``public``.
        """
        ...

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        """Return validation commands for this stack.

        Expected keys: install, build, test, lint, qa, e2e,
        runtime_smoke, ux_smoke, security.
        """
        ...

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        """Return preview/dev-server configuration.

        Expected keys: command, port, health_path, timeout_s.
        """
        ...


# ---------------------------------------------------------------------------
# Validation plan keys (canonical order)
# ---------------------------------------------------------------------------

_VALIDATION_KEYS = (
    "install", "build", "test", "lint", "qa",
    "e2e", "runtime_smoke", "ux_smoke", "security",
)


def _empty_validation_plan() -> dict[str, list[str]]:
    return {k: [] for k in _VALIDATION_KEYS}


def _python_package_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "signalos_product"
    if cleaned[0].isdigit():
        cleaned = f"product_{cleaned}"
    return cleaned


def _detect_python_package(repo_root: Path) -> str:
    src = repo_root / "src"
    if src.is_dir():
        packages = [
            child.name
            for child in src.iterdir()
            if child.is_dir() and (child / "__init__.py").is_file()
        ]
        if packages:
            return sorted(packages)[0]

    profile_path = repo_root / ".signalos" / "profile.json"
    if profile_path.is_file():
        try:
            data = json.loads(profile_path.read_text(encoding="utf-8"))
            package = str(data.get("package", "")).strip()
            if package:
                return _python_package_name(package)
        except (json.JSONDecodeError, OSError):
            pass

    return _python_package_name(repo_root.name)


def _to_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []

    def _emit_section(section: dict[str, Any], prefix: str = "") -> None:
        for key, value in section.items():
            toml_key = _toml_key(key)
            full_key = f"{prefix}.{toml_key}" if prefix else toml_key
            if isinstance(value, dict):
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(f"[{full_key}]")
                _emit_section(value, full_key)
            else:
                lines.append(f"{toml_key} = {_toml_value(value)}")

    _emit_section(data)
    return "\n".join(lines).rstrip() + "\n"


def _toml_key(value: Any) -> str:
    key = str(value)
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return json.dumps(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if value is None:
        return '""'
    return json.dumps(str(value))


def _write_profile_meta(
    signalos_dir: Path,
    profile_meta: dict[str, Any],
    created: list[str],
) -> None:
    """Write ``.signalos/profile.json`` and record it in ``created``.

    Behavior-preserving extraction of the identical write/append block that
    every adapter ran after building its ``profile_meta`` dict. The written
    JSON content and the ``created`` entry are byte-identical to before.
    """
    (signalos_dir / "profile.json").write_text(
        json.dumps(profile_meta, indent=2) + "\n", encoding="utf-8"
    )
    created.append(".signalos/profile.json")


# ---------------------------------------------------------------------------
# ReactViteAdapter
# ---------------------------------------------------------------------------
# Delivery infrastructure templates — owned by SignalOS, not agents.
# These define HOW to deliver (build tooling, config, entry points).
# Agents write WHAT to deliver (components, logic, tests) inside this shell.
# ---------------------------------------------------------------------------

_PACKAGE_JSON_TEMPLATE: dict[str, Any] = {
    "name": "signalos-product",
    "private": True,
    "version": "0.0.0",
    "type": "module",
    "scripts": {
        "dev": "vite",
        "build": "tsc && vite build",
        "preview": "vite preview",
        "test": "vitest run",
    },
    "dependencies": {
        "react": "^18.3.1",
        "react-dom": "^18.3.1",
        "react-router-dom": "^6.23.0",
    },
    "devDependencies": {
        "@types/react": "^18.3.1",
        "@types/react-dom": "^18.3.1",
        "@vitejs/plugin-react": "^4.3.0",
        "typescript": "^5.4.0",
        "vite": "^5.4.0",
        "vitest": "^3.2.0",
        "@testing-library/react": "^16.0.0",
        "@testing-library/dom": "^10.4.0",
        "@testing-library/jest-dom": "^6.4.0",
        # #40: the interaction-test prompt explicitly permits `userEvent`, so
        # user-event MUST ship in the scaffold -- otherwise every test that
        # reaches for it fails to resolve (TS2307) and vitest collects ZERO
        # tests. Surfaced by the funded e2e (a generated test imported it and
        # the whole file failed to collect). The repair loop already knew to
        # add it on TS2307, but shipping it upfront is the correct default.
        "@testing-library/user-event": "^14.5.2",
        "jsdom": "^24.0.0",
    },
}

# CommonJS config (vite.config.cjs), NOT vite.config.ts. Under the funded
# read-only workspace mount, a TS/ESM Vite config makes Vite esbuild-bundle it
# and write a `vite.config.*.timestamp-*.mjs` sidecar NEXT TO the config at the
# workspace root -- and that write throws EROFS, so the graded `npm test` /
# `vite build` cannot even LOAD the config, for every model regardless of skill
# (observed: 229 EROFS in a funded run; the graded runner never started). A
# `.cjs` config is loaded by Node via require -- no esbuild bundle, no sidecar,
# no root write -- so it loads cleanly under the read-only mount. Validated in a
# node:20 container with the workspace bind-mounted :ro. The interop guard
# handles plugin builds that export the plugin as `module.exports = fn` OR as
# `exports.default = fn`. defineConfig is intentionally omitted (it is identity
# and importing it pulls Vite's deprecated CJS Node API); `test` is still read
# from the resolved object.
_VITE_CONFIG = """\
/// <reference types="vitest" />
const _react = require('@vitejs/plugin-react');
const react = _react.default || _react;

module.exports = {
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    // Fix #12: register @testing-library/jest-dom matchers for every test so
    // toBeInTheDocument/toBeChecked resolve under tsc and at runtime.
    setupFiles: ['./src/test/setup.ts'],
  },
};
"""

_VITEST_SETUP = """\
// Vitest setup: register @testing-library/jest-dom custom matchers
// (toBeInTheDocument, toBeChecked, ...) for every test. Referenced by
// vite.config.cjs setupFiles.
import '@testing-library/jest-dom';

// #42: jsdom implements neither window.matchMedia nor ResizeObserver, which
// component libraries (Mantine's useMediaQuery / color-scheme hooks, and
// others) call on render -- without these stubs every such component throws
// 'window.matchMedia is not a function' and EVERY test fails at render.
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}
if (typeof globalThis !== 'undefined' && !globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
"""

_INDEX_HTML = """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>SignalOS Product</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

_MAIN_TSX = """\
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
"""

_APP_TSX = """\
function App() {
  return <h1>SignalOS Product</h1>;
}

export default App;
"""

_APP_TEST_TSX = """\
import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import App from './App';

test('renders heading', () => {
  render(<App />);
  expect(screen.getByText('SignalOS Product')).toBeDefined();
});
"""

_TSCONFIG: dict[str, Any] = {
    "compilerOptions": {
        "target": "ES2020",
        "useDefineForClassFields": True,
        "lib": ["ES2020", "DOM", "DOM.Iterable"],
        "module": "ESNext",
        "skipLibCheck": True,
        "moduleResolution": "bundler",
        "allowImportingTsExtensions": True,
        "isolatedModules": True,
        "moduleDetection": "force",
        "noEmit": True,
        "jsx": "react-jsx",
        "strict": True,
        "noUnusedLocals": True,
        "noUnusedParameters": True,
        "noFallthroughCasesInSwitch": True,
        # #32: vitest runs with globals:true, but tsc only knows about
        # describe/test/it/expect/vi if the vitest ambient types are loaded --
        # otherwise every generated test fails tsc with "Cannot find name
        # 'describe'/'test'/'expect'". jest-dom adds the .toBeInTheDocument()
        # matcher types used by the setup file.
        "types": ["vitest/globals", "@testing-library/jest-dom"],
    },
    "include": ["src"],
}


# ---------------------------------------------------------------------------

# Conventional Vite/Vitest config stems x extensions. Detection must be
# extension-agnostic: the funded scaffold ships `.cjs` (the only form that loads
# under the read-only workspace mount without an EROFS timestamp sidecar), and a
# model may legitimately author any of these. The prior detectors hardcoded only
# `.ts`/`.js`, so the shipped `.cjs` scaffold read as "no vite config".
_VITE_CONFIG_STEMS = ("vite.config", "vitest.config")
_VITE_CONFIG_EXTS = (".ts", ".js", ".cjs", ".mjs", ".cts", ".mts")


def _has_vite_config(repo_root: Path) -> bool:
    """True when a Vite/Vitest config exists under any conventional extension."""
    return any(
        (repo_root / f"{stem}{ext}").is_file()
        for stem in _VITE_CONFIG_STEMS
        for ext in _VITE_CONFIG_EXTS
    )


@dataclass
class ReactViteAdapter:
    """Adapter for React + Vite projects."""

    id: str = "react-vite"
    display_name: str = "React + Vite"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        info: dict[str, Any] = {
            "profile": self.id,
            "can_deliver_ui": True,
            "can_deliver_runnable": True,
            "signals": [],
        }
        pkg_path = repo_root / "package.json"
        if pkg_path.is_file():
            info["signals"].append("package.json")
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "vite" in all_deps:
                    info["signals"].append("vite-dep")
                if "react" in all_deps:
                    info["signals"].append("react-dep")
            except (json.JSONDecodeError, OSError):
                pass
        if _has_vite_config(repo_root):
            info["signals"].append("vite-config")
        if (repo_root / "src").is_dir():
            info["signals"].append("src-dir")
        return info

    def scaffold(
        self,
        repo_root: Path,
        intent: dict[str, Any],
        dependencies: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create delivery infrastructure for a React+Vite project.

        SignalOS owns the delivery shell (build tooling, config, entry points).
        Agents write product code (components, logic, tests) INSIDE this shell.
        """
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        # Delivery infrastructure — SignalOS owns this
        pkg = dict(_PACKAGE_JSON_TEMPLATE)
        pkg["dependencies"] = dict(_PACKAGE_JSON_TEMPLATE["dependencies"])
        pkg["devDependencies"] = dict(_PACKAGE_JSON_TEMPLATE["devDependencies"])
        if dependencies:
            pkg["dependencies"].update(dependencies)
        _write("package.json", json.dumps(pkg, indent=2) + "\n")
        _write("vite.config.cjs", _VITE_CONFIG)
        _write("index.html", _INDEX_HTML)
        # Fix #12: ship the vitest setup file the config references so the
        # shell is valid before generation and jest-dom matchers resolve.
        _write("src/test/setup.ts", _VITEST_SETUP)
        _write("src/main.tsx", _MAIN_TSX)
        _write("src/App.tsx", _APP_TSX)
        _write("src/App.test.tsx", _APP_TEST_TSX)
        _write("tsconfig.json", json.dumps(_TSCONFIG, indent=2) + "\n")

        # Governance metadata
        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {"profile": self.id, "display_name": self.display_name}
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": True,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src",
            "tests": "src",
            "config": ".",
            "public": "public",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["install"] = ["npm install --legacy-peer-deps"]
        plan["build"] = ["npm run build"]
        plan["test"] = ["npm test"]
        return plan

    # Optional protocol extensions (consumed via getattr with graceful
    # fallbacks, so adapters that don't define them keep working unchanged).

    def test_file_command(self, repo_root: Path, test_path: str) -> list[str]:
        """argv to run ONE test file (the per-task green gate in the
        subagent-driven build)."""
        npx = "npx.cmd" if os.name == "nt" else "npx"
        return [npx, "vitest", "run", test_path]

    def structured_test_file_command(self, repo_root: Path,
                                     test_path: str) -> tuple[list[str], str]:
        """argv + reporter-kind to run ONE test file in MACHINE-READABLE mode
        (vitest JSON reporter), for the per-CASE convergence signal in the
        subagent-driven build. The gate parses passing/failing case-ids from the
        JSON; a compile failure emits zero passing cases (the worst state), so
        the passing-set grows once the build starts compiling."""
        npx = "npx.cmd" if os.name == "nt" else "npx"
        return ([npx, "vitest", "run", test_path, "--reporter=json"], "vitest-json")

    def structured_suite_command(self, repo_root: Path,
                                 test_paths: list) -> tuple[list[str], str]:
        """argv + reporter-kind to run MANY acceptance test files in ONE
        machine-readable invocation, for the WHOLE-BUILD convergence signal
        (aggregate passing case-ids across all acceptance tests each integration
        cycle -- so a cross-task regression that drops an earlier green case is
        caught)."""
        npx = "npx.cmd" if os.name == "nt" else "npx"
        return ([npx, "vitest", "run", *test_paths, "--reporter=json"], "vitest-json")

    def prompt_gotchas(self, repo_root: Path) -> str:
        """Stack-specific conventions injected into build-agent prompts, so the
        prompt layer stays stack-agnostic (mirrors design.UILibraryAdapter's
        prompt_desc pattern)."""
        return (
            "Stack gotchas (react-vite + vitest) -- obey to avoid wasted build cycles:\n"
            "- Tests use VITEST: describe/it/test/expect are GLOBAL (no import). For "
            "mocks use `vi` from 'vitest' -- never `jest`.\n"
            "- The react-jsx transform is automatic: do NOT `import React` (unused React "
            "fails noUnusedLocals).\n"
            "- There is NO `@` path alias: import with relative paths only (./types, "
            "./components/Foo). `@/...` will not resolve (TS2307).\n"
            "- Only import packages already in package.json/node_modules; never invent a "
            "module a test imports without creating that module under the source tree.\n"
        )

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "npm run dev",
            "port": 5173,
            "health_path": "/",
            "timeout_s": 30,
        }


# ---------------------------------------------------------------------------
# GenericAdapter
# ---------------------------------------------------------------------------

@dataclass
class GenericAdapter:
    """Generic stdlib Python product adapter.

    This is the fallback real-product path for prompts that do not require a
    dedicated UI stack. It creates runnable Python source and unittest
    structure without external dependencies.
    """

    id: str = "generic"
    display_name: str = "Generic Python Product Repo"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        return {
            "profile": self.id,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
            "signals": ["pyproject.toml"] if (repo_root / "pyproject.toml").is_file() else [],
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []
        package = _python_package_name(intent.get("product_name") or repo_root.name)

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        pyproject = {
            "project": {
                "name": package.replace("_", "-"),
                "version": "0.1.0",
                "description": "SignalOS generated generic product package",
                "requires-python": ">=3.11",
                "dependencies": [],
            },
            "tool": {
                "signalos": {
                    "profile": self.id,
                    "package": package,
                },
            },
        }
        _write("pyproject.toml", _to_toml(pyproject))
        _write(f"src/{package}/__init__.py", f'"""Generated product package: {package}."""\n')
        _write("tests/__init__.py", "")

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "package": package,
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        package = _detect_python_package(repo_root)
        return {
            "source": f"src/{package}",
            "tests": "tests",
            "config": "",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["build"] = [
            "python -c \"from pathlib import Path; assert Path('src').is_dir() and Path('tests').is_dir(), 'missing src/tests'\"",
            "python -m compileall src tests",
        ]
        plan["test"] = [
            "python -c \"from pathlib import Path; assert any(Path('tests').glob('test_*.py')), 'missing generated tests'\"",
            "python -m unittest discover -s tests",
        ]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": None,
            "port": None,
            "health_path": None,
            "timeout_s": None,
        }


# ---------------------------------------------------------------------------
# NodeApiAdapter
# ---------------------------------------------------------------------------

_NODE_PACKAGE_JSON_TEMPLATE: dict[str, Any] = {
    "name": "signalos-node-api-product",
    "private": True,
    "version": "0.0.0",
    "type": "module",
    "scripts": {
        "start": "node src/server.js",
        "build": "node --check src/app.js && node --check src/server.js",
        "test": "node --test",
    },
    "dependencies": {
        "express": "^4.19.2",
    },
    "devDependencies": {},
}

_NODE_APP_JS = """\
import express from 'express';

export function createApp() {
  const app = express();
  app.use(express.json());

  app.get('/health', (_req, res) => {
    res.json({ status: 'ok', service: 'signalos-product-api' });
  });

  app.get('/', (_req, res) => {
    res.json({ product: 'SignalOS Product API', status: 'running' });
  });

  return app;
}

export default createApp;
"""

_NODE_SERVER_JS = """\
import { createApp } from './app.js';

const port = Number(process.env.PORT || 3000);
const server = createApp().listen(port, () => {
  console.log(`SignalOS product API listening on ${port}`);
});

function shutdown() {
  server.close(() => process.exit(0));
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
"""

_NODE_HEALTH_TEST_JS = """\
import test from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../src/app.js';

test('health endpoint responds', async () => {
  const server = createApp().listen(0);
  const address = server.address();
  const port = typeof address === 'object' && address ? address.port : 0;

  try {
    const response = await fetch(`http://127.0.0.1:${port}/health`);
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.status, 'ok');
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
"""

_GO_MOD = """\
module signalos/product

go 1.22
"""

_GO_APP_GO = """\
package app

import (
    "encoding/json"
    "net/http"
)

func NewHandler() http.Handler {
    mux := http.NewServeMux()
    mux.HandleFunc("/health", healthHandler)
    mux.HandleFunc("/", rootHandler)
    return mux
}

func Health() map[string]string {
    return map[string]string{
        "status": "ok",
        "service": "signalos-go-api",
    }
}

func healthHandler(w http.ResponseWriter, _ *http.Request) {
    writeJSON(w, http.StatusOK, Health())
}

func rootHandler(w http.ResponseWriter, _ *http.Request) {
    writeJSON(w, http.StatusOK, map[string]string{
        "product": "SignalOS Go API",
        "status": "running",
    })
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(status)
    _ = json.NewEncoder(w).Encode(payload)
}
"""

_GO_MAIN_GO = """\
package main

import (
    "errors"
    "log"
    "net/http"
    "os"

    "signalos/product/internal/app"
)

func main() {
    port := os.Getenv("PORT")
    if port == "" {
        port = "8080"
    }

    server := &http.Server{
        Addr:    "127.0.0.1:" + port,
        Handler: app.NewHandler(),
    }

    log.Printf("SignalOS Go API listening on %s", server.Addr)
    if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
        log.Fatal(err)
    }
}
"""

_GO_APP_TEST_GO = """\
package app

import (
    "encoding/json"
    "net/http"
    "net/http/httptest"
    "testing"
)

func TestHealthEndpointReturnsOK(t *testing.T) {
    req := httptest.NewRequest(http.MethodGet, "/health", nil)
    rec := httptest.NewRecorder()

    NewHandler().ServeHTTP(rec, req)

    if rec.Code != http.StatusOK {
        t.Fatalf("status code = %d, want %d", rec.Code, http.StatusOK)
    }

    var body map[string]string
    if err := json.NewDecoder(rec.Body).Decode(&body); err != nil {
        t.Fatalf("decode response: %v", err)
    }
    if body["status"] != "ok" {
        t.Fatalf("status = %q, want ok", body["status"])
    }
}
"""


_FASTAPI_PYPROJECT_TEMPLATE: dict[str, Any] = {
    "build-system": {
        "requires": ["setuptools>=68"],
        "build-backend": "setuptools.build_meta",
    },
    "project": {
        "name": "signalos-fastapi-product",
        "version": "0.1.0",
        "description": "SignalOS generated FastAPI product service",
        "requires-python": ">=3.10",
        "dependencies": [
            "fastapi>=0.110,<1",
            "uvicorn[standard]>=0.27,<1",
        ],
        "optional-dependencies": {
            "dev": [
                "pytest>=8,<9",
                "httpx>=0.27,<1",
            ],
        },
    },
    "tool": {
        "setuptools": {"package-dir": {"": "src"}},
        "setuptools.packages.find": {"where": ["src"]},
        "pytest.ini_options": {"testpaths": ["tests"]},
    },
}

_FASTAPI_APP_PY = """\
from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="SignalOS FastAPI Product")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
"""

_FASTAPI_MAIN_PY = """\
from __future__ import annotations

import uvicorn

from .app import app


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
"""

_FASTAPI_HEALTH_TEST_PY = """\
from fastapi.testclient import TestClient

from signalos_product_fastapi.app import app


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
"""


_DOTNET_DEFAULT_TARGET_FRAMEWORK = "net8.0"

_DOTNET_API_CSPROJ_TEMPLATE = """\
<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>{target_framework}</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
</Project>
"""

_DOTNET_PROGRAM_CS = """\
using SignalOSProduct.Api;

if (args.Contains("--self-test", StringComparer.OrdinalIgnoreCase))
{
    var health = ProductRoutes.Health();
    if (!health.TryGetValue("status", out var status) || status != "ok")
    {
        Console.Error.WriteLine("Health self-test failed.");
        return 1;
    }

    Console.WriteLine("Health self-test passed.");
    return 0;
}

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

ProductRoutes.Map(app);

app.Run();
return 0;
"""

_DOTNET_PRODUCT_ROUTES_CS = """\
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;

namespace SignalOSProduct.Api;

public static class ProductRoutes
{
    public static IReadOnlyDictionary<string, string> Health() =>
        new Dictionary<string, string>
        {
            ["status"] = "ok",
            ["service"] = "signalos-dotnet-minimal-api"
        };

    public static void Map(WebApplication app)
    {
        app.MapGet("/health", () => Results.Json(Health()));
        app.MapGet("/", () => Results.Json(new
        {
            product = "SignalOS .NET Minimal API",
            status = "running"
        }));
    }
}
"""


def _resolve_dotnet_target_framework() -> str:
    """Choose an installed ASP.NET Core runtime target when available."""
    try:
        result = subprocess.run(
            ["dotnet", "--list-runtimes"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return _DOTNET_DEFAULT_TARGET_FRAMEWORK
    if result.returncode != 0:
        return _DOTNET_DEFAULT_TARGET_FRAMEWORK

    majors: list[int] = []
    for line in result.stdout.splitlines():
        if not line.startswith("Microsoft.AspNetCore.App "):
            continue
        match = re.search(r"\s(\d+)\.\d+\.\d+\s+\[", line)
        if match:
            major = int(match.group(1))
            if major >= 6:
                majors.append(major)
    if not majors:
        return _DOTNET_DEFAULT_TARGET_FRAMEWORK

    preferred = [major for major in majors if major >= 8]
    selected = max(preferred or majors)
    return f"net{selected}.0"


def _dotnet_api_csproj() -> str:
    return _DOTNET_API_CSPROJ_TEMPLATE.format(
        target_framework=_resolve_dotnet_target_framework()
    )


@dataclass
class NodeApiAdapter:
    """Adapter for Node.js API products.

    This is a non-.NET backend/API path with runnable validation and runtime
    proof. Product-specific routes are generated by agents inside src/ and
    tests/ according to the generation packet.
    """

    id: str = "node-api"
    display_name: str = "Node.js API"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        info: dict[str, Any] = {
            "profile": self.id,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
            "signals": [],
        }
        pkg_path = repo_root / "package.json"
        if pkg_path.is_file():
            info["signals"].append("package.json")
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                scripts = pkg.get("scripts", {})
                if "express" in all_deps:
                    info["signals"].append("express-dep")
                if "start" in scripts:
                    info["signals"].append("start-script")
            except (json.JSONDecodeError, OSError):
                pass
        if (repo_root / "src" / "server.js").is_file():
            info["signals"].append("server-entry")
        if (repo_root / "tests").is_dir():
            info["signals"].append("tests-dir")
        return info

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("package.json", json.dumps(_NODE_PACKAGE_JSON_TEMPLATE, indent=2) + "\n")
        _write("src/app.js", _NODE_APP_JS)
        _write("src/server.js", _NODE_SERVER_JS)
        _write("tests/health.test.js", _NODE_HEALTH_TEST_JS)

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional Node.js API adapter; not .NET/Go-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src",
            "tests": "tests",
            "config": ".",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["install"] = ["npm install --legacy-peer-deps"]
        plan["build"] = ["node --check src/app.js", "node --check src/server.js"]
        plan["test"] = ["npm test"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "npm start",
            "port": 3000,
            "health_path": "/health",
            "timeout_s": 30,
        }


# ---------------------------------------------------------------------------
# GoApiAdapter
# ---------------------------------------------------------------------------

@dataclass
class GoApiAdapter:
    """Adapter for optional Go backend/API products."""

    id: str = "go-api"
    display_name: str = "Go API"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        if (repo_root / "go.mod").is_file():
            signals.append("go.mod")
        if (repo_root / "cmd" / "server" / "main.go").is_file():
            signals.append("server-entry")
        if (repo_root / "internal" / "app" / "app.go").is_file():
            signals.append("app-handler")
        if any((repo_root / "internal" / "app").glob("*_test.go")):
            signals.append("go-tests")
        return {
            "profile": self.id,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("go.mod", _GO_MOD)
        _write("cmd/server/main.go", _GO_MAIN_GO)
        _write("internal/app/app.go", _GO_APP_GO)
        _write("internal/app/app_test.go", _GO_APP_TEST_GO)
        _write("tests/acceptance-map.md", "# Acceptance Map\n\n- Pending: generated Go route tests.\n")

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional Go API adapter; not .NET-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "internal/app",
            "tests": "internal/app",
            "config": ".",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["build"] = ["go test ./..."]
        plan["test"] = ["go test ./..."]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "go run ./cmd/server",
            "port": 8080,
            "health_path": "/health",
            "timeout_s": 30,
        }


# ---------------------------------------------------------------------------
# DotNetMinimalApiAdapter
# ---------------------------------------------------------------------------

@dataclass
class DotNetMinimalApiAdapter:
    """Adapter for optional .NET Minimal API backend/API products."""

    id: str = "dotnet-minimal-api"
    display_name: str = ".NET Minimal API"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        if _has_dotnet_project(repo_root):
            signals.append("csproj")
        if (repo_root / "SignalOSProduct.Api" / "Program.cs").is_file():
            signals.append("minimal-api-program")
        if (repo_root / "SignalOSProduct.Api" / "ProductRoutes.cs").is_file():
            signals.append("product-routes")
        return {
            "profile": self.id,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("SignalOSProduct.Api/SignalOSProduct.Api.csproj", _dotnet_api_csproj())
        _write("SignalOSProduct.Api/Program.cs", _DOTNET_PROGRAM_CS)
        _write("SignalOSProduct.Api/ProductRoutes.cs", _DOTNET_PRODUCT_ROUTES_CS)
        _write("tests/acceptance-map.md", "# Acceptance Map\n\n- Pending: generated route tests.\n")

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional .NET Minimal API adapter; not ABP-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "SignalOSProduct.Api",
            "tests": "tests",
            "config": ".",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        project = "SignalOSProduct.Api/SignalOSProduct.Api.csproj"
        plan["install"] = [f"dotnet restore {project}"]
        plan["build"] = [f"dotnet build {project} --no-restore"]
        plan["test"] = [f"dotnet run --project {project} --no-build -- --self-test"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        project = "SignalOSProduct.Api/SignalOSProduct.Api.csproj"
        return {
            "command": f"dotnet run --project {project} --no-build -- --urls http://127.0.0.1:5050",
            "port": 5050,
            "health_path": "/health",
            "timeout_s": 30,
        }


# ---------------------------------------------------------------------------
# FastApiAdapter
# ---------------------------------------------------------------------------

@dataclass
class FastApiAdapter:
    """Adapter for Python FastAPI backend/API products."""

    id: str = "fastapi-api"
    display_name: str = "FastAPI API"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        info: dict[str, Any] = {
            "profile": self.id,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
            "signals": [],
        }
        pyproject = repo_root / "pyproject.toml"
        if pyproject.is_file():
            info["signals"].append("pyproject.toml")
            try:
                content = pyproject.read_text(encoding="utf-8").lower()
                if "fastapi" in content:
                    info["signals"].append("fastapi-dep")
                if "uvicorn" in content:
                    info["signals"].append("uvicorn-dep")
            except OSError:
                pass
        if (repo_root / "src" / "signalos_product_fastapi" / "app.py").is_file():
            info["signals"].append("fastapi-app")
        if (repo_root / "tests").is_dir():
            info["signals"].append("tests-dir")
        return info

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("pyproject.toml", _to_toml(_FASTAPI_PYPROJECT_TEMPLATE))
        _write("src/signalos_product_fastapi/__init__.py", "")
        _write("src/signalos_product_fastapi/app.py", _FASTAPI_APP_PY)
        _write("src/signalos_product_fastapi/main.py", _FASTAPI_MAIN_PY)
        _write("src/signalos_product_fastapi/routes/__init__.py", "")
        _write("src/signalos_product_fastapi/models/__init__.py", "")
        _write("tests/test_health.py", _FASTAPI_HEALTH_TEST_PY)

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional FastAPI adapter; not .NET/Go-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src/signalos_product_fastapi",
            "tests": "tests",
            "config": ".",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["install"] = ['python -m pip install -e ".[dev]"']
        plan["build"] = ["python -m compileall src tests"]
        plan["test"] = ["python -m pytest"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "python -m uvicorn signalos_product_fastapi.app:app --host 127.0.0.1 --port 8000",
            "port": 8000,
            "health_path": "/health",
            "timeout_s": 30,
        }


# ---------------------------------------------------------------------------
# AngularAdapter
# ---------------------------------------------------------------------------

_ANGULAR_PACKAGE_JSON_TEMPLATE: dict[str, Any] = {
    "name": "signalos-angular-product",
    "private": True,
    "version": "0.0.0",
    "scripts": {
        "start": "ng serve --host 127.0.0.1 --port 4200",
        "build": "ng build",
        "test": "ng test --watch=false --browsers=ChromeHeadless",
    },
    "dependencies": {
        "@angular/animations": "^18.2.0",
        "@angular/common": "^18.2.0",
        "@angular/compiler": "^18.2.0",
        "@angular/core": "^18.2.0",
        "@angular/forms": "^18.2.0",
        "@angular/platform-browser": "^18.2.0",
        "@angular/platform-browser-dynamic": "^18.2.0",
        "@angular/router": "^18.2.0",
        "rxjs": "^7.8.1",
        "tslib": "^2.6.3",
        "zone.js": "^0.14.10",
    },
    "devDependencies": {
        "@angular-devkit/build-angular": "^18.2.0",
        "@angular/cli": "^18.2.0",
        "@angular/compiler-cli": "^18.2.0",
        "typescript": "~5.5.4",
        "jasmine-core": "^5.2.0",
        "karma": "^6.4.4",
        "karma-chrome-launcher": "^3.2.0",
        "karma-jasmine": "^5.1.0",
        "karma-jasmine-html-reporter": "^2.1.0",
    },
}

_ANGULAR_JSON: dict[str, Any] = {
    "$schema": "./node_modules/@angular/cli/lib/config/schema.json",
    "version": 1,
    "newProjectRoot": "projects",
    "projects": {
        "signalos-angular-product": {
            "projectType": "application",
            "root": "",
            "sourceRoot": "src",
            "prefix": "app",
            "architect": {
                "build": {
                    "builder": "@angular-devkit/build-angular:application",
                    "options": {
                        "outputPath": "dist/signalos-angular-product",
                        "index": "src/index.html",
                        "browser": "src/main.ts",
                        "polyfills": ["zone.js"],
                        "tsConfig": "tsconfig.app.json",
                        "assets": ["src/favicon.ico"],
                        "styles": ["src/styles.css"],
                    },
                },
                "serve": {
                    "builder": "@angular-devkit/build-angular:dev-server",
                    "options": {"buildTarget": "signalos-angular-product:build"},
                },
                "test": {
                    "builder": "@angular-devkit/build-angular:karma",
                    "options": {
                        "polyfills": ["zone.js", "zone.js/testing"],
                        "tsConfig": "tsconfig.spec.json",
                        "assets": ["src/favicon.ico"],
                        "styles": ["src/styles.css"],
                    },
                },
            },
        }
    },
}

_ANGULAR_TSCONFIG: dict[str, Any] = {
    "compileOnSave": False,
    "compilerOptions": {
        "outDir": "./dist/out-tsc",
        "strict": True,
        "noImplicitOverride": True,
        "noPropertyAccessFromIndexSignature": True,
        "noImplicitReturns": True,
        "noFallthroughCasesInSwitch": True,
        "skipLibCheck": True,
        "esModuleInterop": True,
        "sourceMap": True,
        "declaration": False,
        "moduleResolution": "bundler",
        "experimentalDecorators": True,
        "importHelpers": True,
        "target": "ES2022",
        "module": "ES2022",
        "useDefineForClassFields": False,
        "lib": ["ES2022", "dom"],
    },
    "angularCompilerOptions": {
        "enableI18nLegacyMessageIdFormat": False,
        "strictInjectionParameters": True,
        "strictInputAccessModifiers": True,
        "strictTemplates": True,
    },
}

_ANGULAR_MAIN_TS = """\
import { bootstrapApplication } from '@angular/platform-browser';
import { AppComponent } from './app/app.component';

bootstrapApplication(AppComponent).catch((err) => console.error(err));
"""

_ANGULAR_APP_COMPONENT_TS = """\
import { Component } from '@angular/core';

@Component({
  selector: 'app-root',
  standalone: true,
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
})
export class AppComponent {
  readonly title = 'SignalOS Product';
}
"""

_ANGULAR_APP_COMPONENT_HTML = """\
<main class="shell">
  <h1>{{ title }}</h1>
  <p>Generated product shell governed by SignalOS.</p>
</main>
"""

_ANGULAR_APP_COMPONENT_SPEC = """\
import { TestBed } from '@angular/core/testing';
import { AppComponent } from './app.component';

describe('AppComponent', () => {
  it('renders the SignalOS product shell', async () => {
    await TestBed.configureTestingModule({ imports: [AppComponent] }).compileComponents();
    const fixture = TestBed.createComponent(AppComponent);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('SignalOS Product');
  });
});
"""


@dataclass
class AngularAdapter:
    """Adapter for optional Angular browser products."""

    id: str = "angular"
    display_name: str = "Angular"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        pkg_path = repo_root / "package.json"
        if pkg_path.is_file():
            signals.append("package.json")
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "@angular/core" in all_deps:
                    signals.append("angular-core-dep")
                if "@angular/cli" in all_deps:
                    signals.append("angular-cli-dep")
            except (json.JSONDecodeError, OSError):
                pass
        if (repo_root / "angular.json").is_file():
            signals.append("angular.json")
        if (repo_root / "src" / "main.ts").is_file():
            signals.append("angular-main")
        return {
            "profile": self.id,
            "can_deliver_ui": True,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("package.json", json.dumps(_ANGULAR_PACKAGE_JSON_TEMPLATE, indent=2) + "\n")
        _write("angular.json", json.dumps(_ANGULAR_JSON, indent=2) + "\n")
        _write("tsconfig.json", json.dumps(_ANGULAR_TSCONFIG, indent=2) + "\n")
        _write("tsconfig.app.json", json.dumps({"extends": "./tsconfig.json", "files": ["src/main.ts"]}, indent=2) + "\n")
        _write("tsconfig.spec.json", json.dumps({"extends": "./tsconfig.json", "include": ["src/**/*.spec.ts", "src/**/*.d.ts"]}, indent=2) + "\n")
        _write("src/index.html", "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>SignalOS Product</title><base href=\"/\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"></head><body><app-root></app-root></body></html>\n")
        _write("src/main.ts", _ANGULAR_MAIN_TS)
        _write("src/styles.css", "body { margin: 0; font-family: system-ui, sans-serif; }\n.shell { padding: 2rem; }\n")
        _write("src/favicon.ico", "")
        _write("src/app/app.component.ts", _ANGULAR_APP_COMPONENT_TS)
        _write("src/app/app.component.html", _ANGULAR_APP_COMPONENT_HTML)
        _write("src/app/app.component.css", ".shell { color: #172033; }\n")
        _write("src/app/app.component.spec.ts", _ANGULAR_APP_COMPONENT_SPEC)

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional Angular adapter; not .NET/ABP-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": True,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src/app",
            "tests": "src/app",
            "config": ".",
            "public": "src",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["install"] = ["npm install --legacy-peer-deps"]
        plan["build"] = ["npm run build"]
        plan["test"] = ["npm test"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "npm start",
            "port": 4200,
            "health_path": "/",
            "timeout_s": 45,
        }


# ---------------------------------------------------------------------------
# NextJsAdapter
# ---------------------------------------------------------------------------

_NEXT_PACKAGE_JSON_TEMPLATE: dict[str, Any] = {
    "name": "signalos-nextjs-product",
    "private": True,
    "version": "0.0.0",
    "type": "module",
    "scripts": {
        "dev": "next dev",
        "build": "next build",
        "test": "vitest run",
    },
    "dependencies": {
        "next": "^15.0.0",
        "react": "^19.0.0",
        "react-dom": "^19.0.0",
    },
    "devDependencies": {
        "@testing-library/dom": "^10.4.0",
        "@testing-library/jest-dom": "^6.4.0",
        "@testing-library/react": "^16.0.0",
        "@types/node": "^22.0.0",
        "@types/react": "^19.0.0",
        "@types/react-dom": "^19.0.0",
        "jsdom": "^24.0.0",
        "typescript": "^5.4.0",
        "vitest": "^3.2.0",
    },
}

_NEXT_TSCONFIG: dict[str, Any] = {
    "compilerOptions": {
        "target": "ES2022",
        "lib": ["dom", "dom.iterable", "es2022"],
        "allowJs": False,
        "skipLibCheck": True,
        "strict": True,
        "noEmit": True,
        "esModuleInterop": True,
        "module": "esnext",
        "moduleResolution": "bundler",
        "resolveJsonModule": True,
        "isolatedModules": True,
        "jsx": "preserve",
        "incremental": True,
        "plugins": [{"name": "next"}],
    },
    "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
    "exclude": ["node_modules"],
}

_NEXT_LAYOUT_TSX = """\
export const metadata = {
  title: 'SignalOS Product',
  description: 'Governed product generated by SignalOS',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
"""

_NEXT_PAGE_TSX = """\
export default function HomePage() {
  return <main><h1>SignalOS Product</h1></main>;
}
"""

_NEXT_PAGE_TEST_TSX = """\
import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import HomePage from './page';

test('renders heading', () => {
  render(<HomePage />);
  expect(screen.getByText('SignalOS Product')).toBeDefined();
});
"""


@dataclass
class NextJsAdapter:
    """Adapter for optional Next.js browser products."""

    id: str = "nextjs-app"
    display_name: str = "Next.js App"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        pkg_path = repo_root / "package.json"
        if pkg_path.is_file():
            signals.append("package.json")
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "next" in all_deps:
                    signals.append("next-dep")
                if "react" in all_deps:
                    signals.append("react-dep")
            except (json.JSONDecodeError, OSError):
                pass
        if (repo_root / "app" / "page.tsx").is_file():
            signals.append("app-router-page")
        if (repo_root / "next.config.js").is_file() or (repo_root / "next.config.mjs").is_file():
            signals.append("next-config")
        return {
            "profile": self.id,
            "can_deliver_ui": True,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("package.json", json.dumps(_NEXT_PACKAGE_JSON_TEMPLATE, indent=2) + "\n")
        _write("tsconfig.json", json.dumps(_NEXT_TSCONFIG, indent=2) + "\n")
        _write("next-env.d.ts", "/// <reference types=\"next\" />\n/// <reference types=\"next/image-types/global\" />\n")
        _write("next.config.mjs", "const nextConfig = {};\n\nexport default nextConfig;\n")
        _write("app/layout.tsx", _NEXT_LAYOUT_TSX)
        _write("app/page.tsx", _NEXT_PAGE_TSX)
        _write("app/page.test.tsx", _NEXT_PAGE_TEST_TSX)

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional Next.js adapter; not .NET/Go-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": True,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "app",
            "tests": "app",
            "config": ".",
            "public": "public",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["install"] = ["npm install --legacy-peer-deps"]
        plan["build"] = ["npm run build"]
        plan["test"] = ["npm test"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "npm run dev",
            "port": 3000,
            "health_path": "/",
            "timeout_s": 45,
        }


# ---------------------------------------------------------------------------
# VueViteAdapter
# ---------------------------------------------------------------------------

_VUE_PACKAGE_JSON_TEMPLATE: dict[str, Any] = {
    "name": "signalos-vue-product",
    "private": True,
    "version": "0.0.0",
    "type": "module",
    "scripts": {
        "dev": "vite",
        "build": "vue-tsc --noEmit && vite build",
        "test": "vitest run",
    },
    "dependencies": {
        "vue": "^3.5.0",
    },
    "devDependencies": {
        "@vitejs/plugin-vue": "^5.1.0",
        "@vue/test-utils": "^2.4.0",
        "jsdom": "^24.0.0",
        "typescript": "^5.4.0",
        "vite": "^5.4.0",
        "vitest": "^3.2.0",
        "vue-tsc": "^2.1.0",
    },
}

# CommonJS config (vite.config.cjs): loaded by Node via require, so Vite writes
# NO `*.timestamp-*.mjs` sidecar beside it -- avoiding the EROFS that a TS/ESM
# config triggers on the funded read-only workspace mount. See the react-vite
# _VITE_CONFIG note for the full rationale.
_VUE_VITE_CONFIG = """\
const _vue = require('@vitejs/plugin-vue');
const vue = _vue.default || _vue;

module.exports = {
  plugins: [vue()],
  test: {
    environment: 'jsdom',
  },
};
"""

_VUE_TSCONFIG: dict[str, Any] = {
    "compilerOptions": {
        "target": "ES2020",
        "useDefineForClassFields": True,
        "module": "ESNext",
        "moduleResolution": "Bundler",
        "strict": True,
        "jsx": "preserve",
        "resolveJsonModule": True,
        "isolatedModules": True,
        "noEmit": True,
        "lib": ["ES2020", "DOM", "DOM.Iterable"],
        "skipLibCheck": True,
    },
    "include": ["src/**/*.ts", "src/**/*.d.ts", "src/**/*.tsx", "src/**/*.vue"],
}

_VUE_MAIN_TS = """\
import { createApp } from 'vue';
import App from './App.vue';

createApp(App).mount('#app');
"""

_VUE_APP = """\
<template>
  <main class="shell">
    <h1>SignalOS Product</h1>
  </main>
</template>

<style scoped>
.shell {
  padding: 2rem;
  font-family: system-ui, sans-serif;
}
</style>
"""

_VUE_APP_TEST = """\
import { mount } from '@vue/test-utils';
import { expect, test } from 'vitest';
import App from './App.vue';

test('renders heading', () => {
  const wrapper = mount(App);
  expect(wrapper.text()).toContain('SignalOS Product');
});
"""


@dataclass
class VueViteAdapter:
    """Adapter for optional Vue + Vite browser products."""

    id: str = "vue-vite"
    display_name: str = "Vue + Vite"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        pkg_path = repo_root / "package.json"
        if pkg_path.is_file():
            signals.append("package.json")
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "vue" in all_deps:
                    signals.append("vue-dep")
                if "vite" in all_deps:
                    signals.append("vite-dep")
            except (json.JSONDecodeError, OSError):
                pass
        if _has_vite_config(repo_root):
            signals.append("vite-config")
        if (repo_root / "src" / "App.vue").is_file():
            signals.append("vue-app")
        return {
            "profile": self.id,
            "can_deliver_ui": True,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("package.json", json.dumps(_VUE_PACKAGE_JSON_TEMPLATE, indent=2) + "\n")
        _write("vite.config.cjs", _VUE_VITE_CONFIG)
        _write("tsconfig.json", json.dumps(_VUE_TSCONFIG, indent=2) + "\n")
        _write("index.html", "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"UTF-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"><title>SignalOS Product</title></head><body><div id=\"app\"></div><script type=\"module\" src=\"/src/main.ts\"></script></body></html>\n")
        _write("src/main.ts", _VUE_MAIN_TS)
        _write("src/App.vue", _VUE_APP)
        _write("src/App.test.ts", _VUE_APP_TEST)

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional Vue/Vite adapter; not .NET/Go-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": True,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src",
            "tests": "src",
            "config": ".",
            "public": "public",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["install"] = ["npm install --legacy-peer-deps"]
        plan["build"] = ["npm run build"]
        plan["test"] = ["npm test"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "npm run dev",
            "port": 5173,
            "health_path": "/",
            "timeout_s": 30,
        }


# ---------------------------------------------------------------------------
# FlutterAppAdapter
# ---------------------------------------------------------------------------

_FLUTTER_PUBSPEC_YAML = """\
name: signalos_flutter_product
description: SignalOS generated Flutter product.
publish_to: "none"
version: 0.1.0+1

environment:
  sdk: ">=3.3.0 <4.0.0"

dependencies:
  flutter:
    sdk: flutter

dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^4.0.0

flutter:
  uses-material-design: true
"""

_FLUTTER_MAIN_DART = """\
import 'package:flutter/material.dart';

void main() {
  runApp(const SignalOSProductApp());
}

class SignalOSProductApp extends StatelessWidget {
  const SignalOSProductApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'SignalOS Product',
      home: Scaffold(
        appBar: AppBar(title: const Text('SignalOS Product')),
        body: const Center(child: Text('SignalOS Product')),
      ),
    );
  }
}
"""

_FLUTTER_WIDGET_TEST_DART = """\
import 'package:flutter_test/flutter_test.dart';
import 'package:signalos_flutter_product/main.dart';

void main() {
  testWidgets('renders SignalOS product shell', (tester) async {
    await tester.pumpWidget(const SignalOSProductApp());
    expect(find.text('SignalOS Product'), findsWidgets);
  });
}
"""


@dataclass
class FlutterAppAdapter:
    """Adapter for optional Flutter mobile products."""

    id: str = "flutter-app"
    display_name: str = "Flutter App"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        pubspec = repo_root / "pubspec.yaml"
        if pubspec.is_file():
            signals.append("pubspec.yaml")
            try:
                content = pubspec.read_text(encoding="utf-8").lower()
                if "flutter:" in content or "sdk: flutter" in content:
                    signals.append("flutter-dep")
            except OSError:
                pass
        if (repo_root / "lib" / "main.dart").is_file():
            signals.append("flutter-main")
        if (repo_root / "test").is_dir():
            signals.append("flutter-tests")
        return {
            "profile": self.id,
            "can_deliver_ui": True,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("pubspec.yaml", _FLUTTER_PUBSPEC_YAML)
        _write("analysis_options.yaml", "include: package:flutter_lints/flutter.yaml\n")
        _write("lib/main.dart", _FLUTTER_MAIN_DART)
        _write("test/widget_test.dart", _FLUTTER_WIDGET_TEST_DART)

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional Flutter mobile adapter; not .NET/Go/web-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": True,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "lib",
            "tests": "test",
            "config": ".",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["install"] = ["flutter pub get"]
        plan["build"] = ["flutter analyze"]
        plan["test"] = ["flutter test"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "flutter run",
            "port": None,
            "health_path": None,
            "timeout_s": 60,
        }


# ---------------------------------------------------------------------------
# ExpoReactNativeAdapter
# ---------------------------------------------------------------------------

_EXPO_PACKAGE_JSON_TEMPLATE: dict[str, Any] = {
    "name": "signalos-expo-product",
    "private": True,
    "version": "0.0.0",
    "type": "module",
    "scripts": {
        "start": "expo start",
        "build": "node --check App.js && node --check src/productState.js",
        "test": "node --test",
    },
    "dependencies": {
        "expo": "^52.0.0",
        "react": "^18.3.1",
        "react-native": "^0.76.0",
    },
    "devDependencies": {},
}

_EXPO_APP_JSON: dict[str, Any] = {
    "expo": {
        "name": "SignalOS Product",
        "slug": "signalos-product",
        "version": "0.1.0",
        "orientation": "portrait",
        "platforms": ["ios", "android", "web"],
    },
}

_EXPO_APP_JS = """\
import React from 'react';
import { SafeAreaView, Text, StyleSheet } from 'react-native';
import { productTitle } from './src/productState.js';

export default function App() {
  return React.createElement(
    SafeAreaView,
    { style: styles.container },
    React.createElement(Text, { style: styles.title }, productTitle()),
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  title: {
    fontSize: 24,
    fontWeight: '600',
  },
});
"""

_EXPO_PRODUCT_STATE_JS = """\
export function productTitle() {
  return 'SignalOS Product';
}
"""

_EXPO_PRODUCT_STATE_TEST_JS = """\
import test from 'node:test';
import assert from 'node:assert/strict';
import { productTitle } from '../src/productState.js';

test('product title is stable', () => {
  assert.equal(productTitle(), 'SignalOS Product');
});
"""


@dataclass
class ExpoReactNativeAdapter:
    """Adapter for optional Expo React Native mobile products."""

    id: str = "expo-react-native"
    display_name: str = "Expo React Native"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        pkg_path = repo_root / "package.json"
        if pkg_path.is_file():
            signals.append("package.json")
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "expo" in all_deps:
                    signals.append("expo-dep")
                if "react-native" in all_deps:
                    signals.append("react-native-dep")
            except (json.JSONDecodeError, OSError):
                pass
        if (repo_root / "app.json").is_file():
            signals.append("expo-app-json")
        if (repo_root / "App.js").is_file() or (repo_root / "App.tsx").is_file():
            signals.append("react-native-app-entry")
        return {
            "profile": self.id,
            "can_deliver_ui": True,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("package.json", json.dumps(_EXPO_PACKAGE_JSON_TEMPLATE, indent=2) + "\n")
        _write("app.json", json.dumps(_EXPO_APP_JSON, indent=2) + "\n")
        _write("App.js", _EXPO_APP_JS)
        _write("src/productState.js", _EXPO_PRODUCT_STATE_JS)
        _write("tests/productState.test.js", _EXPO_PRODUCT_STATE_TEST_JS)

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional Expo React Native mobile adapter; not .NET/Go/web-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": True,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src",
            "tests": "tests",
            "config": ".",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["install"] = ["npm install --legacy-peer-deps"]
        plan["build"] = ["npm run build"]
        plan["test"] = ["npm test"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "npm start",
            "port": 8081,
            "health_path": None,
            "timeout_s": 60,
        }


# ---------------------------------------------------------------------------
# RustApiAdapter
# ---------------------------------------------------------------------------

_RUST_CARGO_TOML = """\
[package]
name = "signalos-rust-api-product"
version = "0.1.0"
edition = "2021"

[dependencies]
"""

_RUST_LIB_RS = """\
pub fn health_payload() -> &'static str {
    r#"{"status":"ok","service":"signalos-rust-api"}"#
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn health_payload_reports_ok() {
        assert!(health_payload().contains(r#""status":"ok""#));
    }
}
"""

_RUST_MAIN_RS = """\
use std::io::{Read, Write};
use std::net::TcpListener;

fn main() -> std::io::Result<()> {
    let port = std::env::var("PORT").unwrap_or_else(|_| "8081".to_string());
    let listener = TcpListener::bind(format!("127.0.0.1:{port}"))?;
    println!("SignalOS Rust API listening on 127.0.0.1:{port}");

    for stream in listener.incoming() {
        let mut stream = stream?;
        let mut buffer = [0; 1024];
        let _ = stream.read(&mut buffer);
        let request = String::from_utf8_lossy(&buffer);
        let body = if request.starts_with("GET /health ") {
            signalos_rust_api_product::health_payload()
        } else {
            r#"{"product":"SignalOS Rust API","status":"running"}"#
        };
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            body.len(),
            body
        );
        stream.write_all(response.as_bytes())?;
    }
    Ok(())
}
"""


@dataclass
class RustApiAdapter:
    """Adapter for optional Rust backend/API products."""

    id: str = "rust-api"
    display_name: str = "Rust API"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        cargo = repo_root / "Cargo.toml"
        if cargo.is_file():
            signals.append("Cargo.toml")
            try:
                if "signalos-rust-api-product" in cargo.read_text(encoding="utf-8"):
                    signals.append("signalos-rust-package")
            except OSError:
                pass
        if (repo_root / "src" / "main.rs").is_file():
            signals.append("rust-main")
        if (repo_root / "src" / "lib.rs").is_file():
            signals.append("rust-lib")
        return {
            "profile": self.id,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("Cargo.toml", _RUST_CARGO_TOML)
        _write("src/lib.rs", _RUST_LIB_RS)
        _write("src/main.rs", _RUST_MAIN_RS)
        _write("tests/acceptance-map.md", "# Acceptance Map\n\n- Pending: generated Rust API route tests.\n")

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional Rust API adapter; not .NET/Go-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src",
            "tests": "tests",
            "config": ".",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["build"] = ["cargo build"]
        plan["test"] = ["cargo test"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "cargo run",
            "port": 8081,
            "health_path": "/health",
            "timeout_s": 45,
        }


# ---------------------------------------------------------------------------
# JavaApiAdapter
# ---------------------------------------------------------------------------

_JAVA_PRODUCT_SERVER = """\
package com.signalos.product;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;

public final class ProductServer {
    private ProductServer() {}

    public static String healthPayload() {
        return "{\"status\":\"ok\",\"service\":\"signalos-java-api\"}";
    }

    public static HttpServer createServer(int port) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 0);
        server.createContext("/health", exchange -> writeJson(exchange, healthPayload()));
        server.createContext("/", exchange -> writeJson(exchange, "{\"product\":\"SignalOS Java API\",\"status\":\"running\"}"));
        return server;
    }

    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8082"));
        HttpServer server = createServer(port);
        server.start();
        System.out.println("SignalOS Java API listening on 127.0.0.1:" + port);
    }

    private static void writeJson(com.sun.net.httpserver.HttpExchange exchange, String body) throws IOException {
        byte[] payload = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(200, payload.length);
        try (OutputStream stream = exchange.getResponseBody()) {
            stream.write(payload);
        }
    }
}
"""

_JAVA_PRODUCT_SERVER_TEST = """\
package com.signalos.product;

public final class ProductServerTest {
    public static void main(String[] args) {
        String payload = ProductServer.healthPayload();
        if (!payload.contains("\"status\":\"ok\"")) {
            throw new AssertionError("health payload must report ok: " + payload);
        }
    }
}
"""


@dataclass
class JavaApiAdapter:
    """Adapter for optional Java backend/API products."""

    id: str = "java-api"
    display_name: str = "Java API"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        if (repo_root / "src" / "main" / "java").is_dir():
            signals.append("java-main-src")
        if (repo_root / "src" / "test" / "java").is_dir():
            signals.append("java-test-src")
        if (repo_root / "pom.xml").is_file():
            signals.append("pom.xml")
        if (repo_root / "build.gradle").is_file() or (repo_root / "build.gradle.kts").is_file():
            signals.append("gradle-build")
        return {
            "profile": self.id,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("src/main/java/com/signalos/product/ProductServer.java", _JAVA_PRODUCT_SERVER)
        _write("src/test/java/com/signalos/product/ProductServerTest.java", _JAVA_PRODUCT_SERVER_TEST)
        _write("tests/acceptance-map.md", "# Acceptance Map\n\n- Pending: generated Java API route tests.\n")

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional Java API adapter; not .NET/Go-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src/main/java/com/signalos/product",
            "tests": "src/test/java/com/signalos/product",
            "config": ".",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        compile_cmd = (
            "javac -d build/classes "
            "src/main/java/com/signalos/product/ProductServer.java "
            "src/test/java/com/signalos/product/ProductServerTest.java"
        )
        plan["build"] = [compile_cmd]
        plan["test"] = [compile_cmd, "java -cp build/classes com.signalos.product.ProductServerTest"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "java -cp build/classes com.signalos.product.ProductServer",
            "port": 8082,
            "health_path": "/health",
            "timeout_s": 30,
        }


# ---------------------------------------------------------------------------
# DjangoApiAdapter
# ---------------------------------------------------------------------------

_DJANGO_PYPROJECT_TEMPLATE: dict[str, Any] = {
    "project": {
        "name": "signalos-django-product",
        "version": "0.1.0",
        "description": "SignalOS generated Django API product",
        "requires-python": ">=3.11",
        "dependencies": [
            "Django>=5.0,<6",
        ],
    },
    "project.optional-dependencies": {
        "dev": [
            "pytest>=8,<9",
        ],
    },
}

_DJANGO_MANAGE_PY = """\
#!/usr/bin/env python
import os
import sys

if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'signalos_product_django.settings')
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
"""

_DJANGO_SETTINGS_PY = """\
SECRET_KEY = 'signalos-local-dev-only'
DEBUG = True
ROOT_URLCONF = 'signalos_product_django.urls'
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']
INSTALLED_APPS = ['signalos_product_django.product']
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
MIDDLEWARE = []
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}
"""

_DJANGO_URLS_PY = """\
from django.http import JsonResponse
from django.urls import path


def health(_request):
    return JsonResponse({'status': 'ok', 'service': 'signalos-django-api'})


urlpatterns = [
    path('health', health),
]
"""

_DJANGO_TEST_HEALTH = """\
from django.test import Client


def test_health_endpoint_reports_ok():
    response = Client().get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'
"""


@dataclass
class DjangoApiAdapter:
    """Adapter for optional Django backend/API products."""

    id: str = "django-api"
    display_name: str = "Django API"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        if (repo_root / "manage.py").is_file():
            signals.append("manage.py")
        pyproject = repo_root / "pyproject.toml"
        if pyproject.is_file():
            signals.append("pyproject.toml")
            try:
                if "django" in pyproject.read_text(encoding="utf-8").lower():
                    signals.append("django-dep")
            except OSError:
                pass
        if (repo_root / "src" / "signalos_product_django" / "settings.py").is_file():
            signals.append("django-settings")
        return {
            "profile": self.id,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("pyproject.toml", _to_toml(_DJANGO_PYPROJECT_TEMPLATE))
        _write("manage.py", _DJANGO_MANAGE_PY)
        _write("src/signalos_product_django/__init__.py", "")
        _write("src/signalos_product_django/settings.py", _DJANGO_SETTINGS_PY)
        _write("src/signalos_product_django/urls.py", _DJANGO_URLS_PY)
        _write("src/signalos_product_django/product/__init__.py", "")
        _write("tests/test_health.py", _DJANGO_TEST_HEALTH)

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional Django API adapter; not .NET/Go-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src/signalos_product_django",
            "tests": "tests",
            "config": ".",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["install"] = ['python -m pip install -e ".[dev]"']
        plan["build"] = ["python -m compileall src tests"]
        plan["test"] = ["python -m pytest"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "python manage.py runserver 127.0.0.1:8001",
            "port": 8001,
            "health_path": "/health",
            "timeout_s": 30,
        }


# ---------------------------------------------------------------------------
# FlaskApiAdapter
# ---------------------------------------------------------------------------

_FLASK_PYPROJECT_TEMPLATE: dict[str, Any] = {
    "project": {
        "name": "signalos-flask-product",
        "version": "0.1.0",
        "description": "SignalOS generated Flask API product",
        "requires-python": ">=3.11",
        "dependencies": [
            "Flask>=3.0,<4",
        ],
    },
    "project.optional-dependencies": {
        "dev": [
            "pytest>=8,<9",
        ],
    },
    "tool": {
        "setuptools": {
            "package-dir": {"": "src"},
            "packages": {"find": {"where": ["src"]}},
        },
    },
}

_FLASK_APP_PY = """\
from flask import Flask, jsonify


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get('/health')
    def health():
        return jsonify({'status': 'ok', 'service': 'signalos-flask-api'})

    @app.get('/')
    def root():
        return jsonify({'product': 'SignalOS Flask API', 'status': 'running'})

    return app
"""

_FLASK_MAIN_PY = """\
from .app import create_app


app = create_app()


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8002)
"""

_FLASK_TEST_HEALTH = """\
from signalos_product_flask.app import create_app


def test_health_endpoint_reports_ok():
    client = create_app().test_client()
    response = client.get('/health')
    assert response.status_code == 200
    assert response.get_json()['status'] == 'ok'
"""


@dataclass
class FlaskApiAdapter:
    """Adapter for optional Flask backend/API products."""

    id: str = "flask-api"
    display_name: str = "Flask API"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        pyproject = repo_root / "pyproject.toml"
        if pyproject.is_file():
            signals.append("pyproject.toml")
            try:
                if "flask" in pyproject.read_text(encoding="utf-8").lower():
                    signals.append("flask-dep")
            except OSError:
                pass
        if (repo_root / "src" / "signalos_product_flask" / "app.py").is_file():
            signals.append("flask-app")
        return {
            "profile": self.id,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("pyproject.toml", _to_toml(_FLASK_PYPROJECT_TEMPLATE))
        _write("src/signalos_product_flask/__init__.py", "")
        _write("src/signalos_product_flask/app.py", _FLASK_APP_PY)
        _write("src/signalos_product_flask/main.py", _FLASK_MAIN_PY)
        _write("src/signalos_product_flask/routes/__init__.py", "")
        _write("tests/test_health.py", _FLASK_TEST_HEALTH)

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional Flask API adapter; not .NET/Go-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src/signalos_product_flask",
            "tests": "tests",
            "config": ".",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["install"] = ['python -m pip install -e ".[dev]"']
        plan["build"] = ["python -m compileall src tests"]
        plan["test"] = ["python -m pytest"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "python -m signalos_product_flask.main",
            "port": 8002,
            "health_path": "/health",
            "timeout_s": 30,
        }


# ---------------------------------------------------------------------------
# NestJsApiAdapter
# ---------------------------------------------------------------------------

_NEST_PACKAGE_JSON_TEMPLATE: dict[str, Any] = {
    "name": "signalos-nestjs-product",
    "private": True,
    "version": "0.0.0",
    "type": "module",
    "scripts": {
        "start": "node dist/main.js",
        "dev": "tsx src/main.ts",
        "build": "tsc -p tsconfig.json",
        "test": "vitest run",
    },
    "dependencies": {
        "@nestjs/common": "^10.4.0",
        "@nestjs/core": "^10.4.0",
        "@nestjs/platform-express": "^10.4.0",
        "reflect-metadata": "^0.2.2",
        "rxjs": "^7.8.0",
    },
    "devDependencies": {
        "@types/node": "^22.0.0",
        "tsx": "^4.19.0",
        "typescript": "^5.4.0",
        "vitest": "^3.2.0",
    },
}

_NEST_TSCONFIG: dict[str, Any] = {
    "compilerOptions": {
        "module": "NodeNext",
        "moduleResolution": "NodeNext",
        "target": "ES2022",
        "outDir": "./dist",
        "rootDir": "./src",
        "strict": True,
        "experimentalDecorators": True,
        "emitDecoratorMetadata": True,
        "esModuleInterop": True,
        "skipLibCheck": True,
    },
    "include": ["src/**/*.ts"],
}

_NEST_MAIN_TS = """\
import 'reflect-metadata';
import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module.js';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  const port = Number(process.env.PORT || 3001);
  await app.listen(port, '127.0.0.1');
}

bootstrap();
"""

_NEST_APP_MODULE_TS = """\
import { Module } from '@nestjs/common';
import { AppController } from './app.controller.js';

@Module({
  controllers: [AppController],
})
export class AppModule {}
"""

_NEST_APP_CONTROLLER_TS = """\
import { Controller, Get } from '@nestjs/common';

@Controller()
export class AppController {
  @Get('health')
  health() {
    return { status: 'ok', service: 'signalos-nestjs-api' };
  }

  @Get()
  root() {
    return { product: 'SignalOS NestJS API', status: 'running' };
  }
}
"""

_NEST_APP_CONTROLLER_TEST_TS = """\
import { describe, expect, it } from 'vitest';
import { AppController } from './app.controller.js';

describe('AppController', () => {
  it('reports health', () => {
    expect(new AppController().health()).toEqual({
      status: 'ok',
      service: 'signalos-nestjs-api',
    });
  });
});
"""


@dataclass
class NestJsApiAdapter:
    """Adapter for optional NestJS backend/API products."""

    id: str = "nestjs-api"
    display_name: str = "NestJS API"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        pkg_path = repo_root / "package.json"
        if pkg_path.is_file():
            signals.append("package.json")
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "@nestjs/core" in all_deps:
                    signals.append("nestjs-core-dep")
            except (json.JSONDecodeError, OSError):
                pass
        if (repo_root / "src" / "app.module.ts").is_file():
            signals.append("nestjs-module")
        return {
            "profile": self.id,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("package.json", json.dumps(_NEST_PACKAGE_JSON_TEMPLATE, indent=2) + "\n")
        _write("tsconfig.json", json.dumps(_NEST_TSCONFIG, indent=2) + "\n")
        _write("src/main.ts", _NEST_MAIN_TS)
        _write("src/app.module.ts", _NEST_APP_MODULE_TS)
        _write("src/app.controller.ts", _NEST_APP_CONTROLLER_TS)
        _write("src/app.controller.spec.ts", _NEST_APP_CONTROLLER_TEST_TS)

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional NestJS API adapter; not .NET/Go-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src",
            "tests": "src",
            "config": ".",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["install"] = ["npm install --legacy-peer-deps"]
        plan["build"] = ["npm run build"]
        plan["test"] = ["npm test"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "npm run dev",
            "port": 3001,
            "health_path": "/health",
            "timeout_s": 45,
        }


# ---------------------------------------------------------------------------
# SpringBootApiAdapter
# ---------------------------------------------------------------------------

_SPRING_POM_XML = """\
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.3.5</version>
    <relativePath/>
  </parent>
  <groupId>com.signalos</groupId>
  <artifactId>signalos-spring-boot-product</artifactId>
  <version>0.1.0</version>
  <name>SignalOS Spring Boot Product</name>
  <properties>
    <java.version>17</java.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-test</artifactId>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
"""

_SPRING_APPLICATION_JAVA = """\
package com.signalos.product;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class ProductApplication {
    public static void main(String[] args) {
        SpringApplication.run(ProductApplication.class, args);
    }
}
"""

_SPRING_HEALTH_CONTROLLER_JAVA = """\
package com.signalos.product;

import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class HealthController {
    @GetMapping("/health")
    public Map<String, String> health() {
        return Map.of("status", "ok", "service", "signalos-spring-boot-api");
    }

    @GetMapping("/")
    public Map<String, String> root() {
        return Map.of("product", "SignalOS Spring Boot API", "status", "running");
    }
}
"""

_SPRING_HEALTH_TEST_JAVA = """\
package com.signalos.product;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class HealthControllerTest {
    @Test
    void healthReportsOk() {
        assertThat(new HealthController().health()).containsEntry("status", "ok");
    }
}
"""


@dataclass
class SpringBootApiAdapter:
    """Adapter for optional Spring Boot backend/API products."""

    id: str = "spring-boot-api"
    display_name: str = "Spring Boot API"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        pom = repo_root / "pom.xml"
        if pom.is_file():
            signals.append("pom.xml")
            try:
                if "spring-boot" in pom.read_text(encoding="utf-8").lower():
                    signals.append("spring-boot-pom")
            except OSError:
                pass
        for gradle_name in ("build.gradle", "build.gradle.kts"):
            gradle = repo_root / gradle_name
            if gradle.is_file():
                signals.append(gradle_name)
                try:
                    if "spring-boot" in gradle.read_text(encoding="utf-8").lower():
                        signals.append("spring-boot-gradle")
                except OSError:
                    pass
        if (repo_root / "src" / "main" / "java").is_dir():
            signals.append("java-main-src")
        return {
            "profile": self.id,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
            "signals": signals,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write("pom.xml", _SPRING_POM_XML)
        _write("src/main/java/com/signalos/product/ProductApplication.java", _SPRING_APPLICATION_JAVA)
        _write("src/main/java/com/signalos/product/HealthController.java", _SPRING_HEALTH_CONTROLLER_JAVA)
        _write("src/test/java/com/signalos/product/HealthControllerTest.java", _SPRING_HEALTH_TEST_JAVA)
        _write("tests/acceptance-map.md", "# Acceptance Map\n\n- Pending: generated Spring Boot API route tests.\n")

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "optional Spring Boot API adapter; not .NET/Go-locked and not the default",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": True,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src/main/java/com/signalos/product",
            "tests": "src/test/java/com/signalos/product",
            "config": ".",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()
        plan["install"] = ["mvn -q -DskipTests dependency:go-offline"]
        plan["build"] = ["mvn -q -DskipTests package"]
        plan["test"] = ["mvn -q test"]
        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": "mvn spring-boot:run",
            "port": 8080,
            "health_path": "/health",
            "timeout_s": 60,
        }


# ---------------------------------------------------------------------------
# AgentSelectedAdapter
# ---------------------------------------------------------------------------

@dataclass
class AgentSelectedAdapter:
    """Adapter for user/agent-selected technologies without a native shell.

    SignalOS writes governance metadata and a stack decision stub, then allows
    the agent to create a conventional repo under src/, tests/, package files,
    and config files. Validation/preview are delegated to existing repo
    detection after the agent has produced real tooling.
    """

    id: str = "agent-selected"
    display_name: str = "Agent-selected Technology"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        delegated = ExistingRepoAdapter().detect(repo_root)
        return {
            "profile": self.id,
            "can_deliver_ui": delegated.get("can_deliver_ui", False),
            "can_deliver_runnable": delegated.get("can_deliver_runnable", False),
            "signals": delegated.get("signals", []),
            "detected_stacks": delegated.get("detected_stacks", []),
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": write LF on every platform. The reviewed funded
            # dependency fixtures are LF, and the broker's materialize step
            # sha-checks the scaffold package.json byte-for-byte against them;
            # without this, Path.write_text translates \n -> \r\n on Windows and
            # the funded run fails "workspace package.json does not match the
            # reviewed scaffold" (Linux/CI is LF, so it only bit Windows hosts).
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(rel)

        _write(
            "PRODUCT_STACK.md",
            "# Product Stack Decision\n\n"
            "SignalOS has not forced a framework for this product. The build "
            "agent must choose or honor the requested technology, record setup "
            "commands, and provide build/test proof before closeout can be ready.\n",
        )
        (repo_root / "src").mkdir(parents=True, exist_ok=True)
        (repo_root / "tests").mkdir(parents=True, exist_ok=True)

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {
            "profile": self.id,
            "display_name": self.display_name,
            "technology_policy": "agent may choose any provable product technology",
        }
        _write_profile_meta(signalos_dir, profile_meta, created)

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": False,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "src",
            "tests": "tests",
            "config": ".",
            "public": "public",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        return ExistingRepoAdapter().validation_plan(repo_root)

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return ExistingRepoAdapter().preview_plan(repo_root)


# ---------------------------------------------------------------------------
# ExistingRepoAdapter
# ---------------------------------------------------------------------------

# Detectable project markers and associated profile hints
_PROJECT_MARKERS: list[tuple[str, str, str]] = [
    # (filename, dependency-key-or-"*", profile-hint)
    ("package.json", "@angular/core", "angular"),
    ("package.json", "@angular/cli", "angular"),
    ("package.json", "next", "nextjs-app"),
    ("package.json", "vue", "vue-vite"),
    ("package.json", "@nestjs/core", "nestjs-api"),
    ("package.json", "expo", "expo-react-native"),
    ("package.json", "react-native", "expo-react-native"),
    ("package.json", "react", "react-vite"),
    ("package.json", "vite", "react-vite"),
    ("package.json", "express", "node-api"),
    ("package.json", "*", "node"),
    ("pyproject.toml", "django", "django-api"),
    ("pyproject.toml", "fastapi", "fastapi-api"),
    ("pyproject.toml", "flask", "flask-api"),
    ("Cargo.toml", "*", "rust-api"),
    ("pubspec.yaml", "flutter", "flutter-app"),
    ("pyproject.toml", "*", "python"),
    ("go.mod", "*", "go"),
    ("pom.xml", "spring-boot", "spring-boot-api"),
    ("pom.xml", "*", "java-api"),
    ("build.gradle", "spring-boot", "spring-boot-api"),
    ("build.gradle", "*", "java-api"),
    ("build.gradle.kts", "spring-boot", "spring-boot-api"),
    ("build.gradle.kts", "*", "java-api"),
]


def _has_dotnet_project(repo_root: Path) -> bool:
    return any(repo_root.glob("*.csproj")) or any(repo_root.glob("*/*.csproj"))


@dataclass
class ExistingRepoAdapter:
    """Adapter for pre-existing repositories.

    Detects the repo's stack but does NOT overwrite source files.
    """

    id: str = "existing-repo"
    display_name: str = "Existing Repository"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        signals: list[str] = []
        detected_stacks: list[str] = []

        if _has_dotnet_project(repo_root):
            signals.append("csproj")
            detected_stacks.append("dotnet-minimal-api")

        for marker_file, dep_key, hint in _PROJECT_MARKERS:
            marker_path = repo_root / marker_file
            if not marker_path.is_file():
                continue
            signals.append(marker_file)
            if dep_key == "*":
                detected_stacks.append(hint)
                continue
            # For package.json, inspect dependencies
            if marker_file == "package.json":
                try:
                    pkg = json.loads(marker_path.read_text(encoding="utf-8"))
                    all_deps = {
                        **pkg.get("dependencies", {}),
                        **pkg.get("devDependencies", {}),
                    }
                    if dep_key in all_deps:
                        signals.append(f"{dep_key}-dep")
                        detected_stacks.append(hint)
                except (json.JSONDecodeError, OSError):
                    pass
            elif marker_file == "pyproject.toml":
                try:
                    content = marker_path.read_text(encoding="utf-8").lower()
                    if dep_key.lower() in content:
                        signals.append(f"{dep_key}-dep")
                        detected_stacks.append(hint)
                except OSError:
                    pass
            else:
                try:
                    content = marker_path.read_text(encoding="utf-8").lower()
                    if dep_key.lower() in content:
                        signals.append(f"{dep_key}-dep")
                        detected_stacks.append(hint)
                except OSError:
                    pass

        has_src = (repo_root / "src").is_dir()
        if has_src:
            signals.append("src-dir")

        can_ui = bool(
            {
                "react-vite",
                "angular",
                "nextjs-app",
                "vue-vite",
                "flutter-app",
                "expo-react-native",
            }
            & set(detected_stacks)
        )
        can_run = bool(detected_stacks)

        return {
            "profile": self.id,
            "can_deliver_ui": can_ui,
            "can_deliver_runnable": can_run,
            "signals": signals,
            "detected_stacks": detected_stacks,
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        """Preserve existing layout -- only create governance metadata."""
        created: list[str] = []
        preserved: list[str] = []

        # Record existing source files as preserved
        for child in repo_root.iterdir():
            if child.name.startswith("."):
                continue
            preserved.append(child.name)

        # Only write governance metadata
        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {"profile": self.id, "display_name": self.display_name}
        _write_profile_meta(signalos_dir, profile_meta, created)

        detection = self.detect(repo_root)
        return {
            "created": created,
            "preserved": preserved,
            "can_deliver_ui": detection["can_deliver_ui"],
            "can_deliver_runnable": detection["can_deliver_runnable"],
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        targets: dict[str, str] = {
            "source": "",
            "tests": "",
            "config": ".",
            "public": "",
        }
        if (repo_root / "src").is_dir():
            targets["source"] = "src"
        if (repo_root / "tests").is_dir():
            targets["tests"] = "tests"
        elif (repo_root / "test").is_dir():
            targets["tests"] = "test"
        elif (repo_root / "src").is_dir():
            targets["tests"] = "src"
        if (repo_root / "public").is_dir():
            targets["public"] = "public"
        return targets

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        plan = _empty_validation_plan()

        pkg_path = repo_root / "package.json"
        if pkg_path.is_file():
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                scripts = pkg.get("scripts", {})
            except (json.JSONDecodeError, OSError):
                scripts = {}

            plan["install"] = ["npm install --legacy-peer-deps"]
            if "build" in scripts:
                plan["build"] = ["npm run build"]
            if "test" in scripts:
                plan["test"] = ["npm test"]
            if "lint" in scripts:
                plan["lint"] = ["npm run lint"]
            return plan

        if (repo_root / "Cargo.toml").is_file():
            plan["install"] = []
            plan["build"] = ["cargo build"]
            plan["test"] = ["cargo test"]
            plan["lint"] = ["cargo clippy"]
            return plan

        if (repo_root / "pyproject.toml").is_file():
            plan["install"] = ["pip install -e '.[dev]'"]
            plan["test"] = ["pytest"]
            plan["lint"] = ["ruff check ."]
            return plan

        if (repo_root / "go.mod").is_file():
            plan["build"] = ["go build ./..."]
            plan["test"] = ["go test ./..."]
            plan["lint"] = ["golangci-lint run"]
            return plan

        dotnet_projects = sorted(repo_root.glob("*.csproj")) or sorted(repo_root.glob("*/*.csproj"))
        if dotnet_projects:
            rel_project = dotnet_projects[0].relative_to(repo_root).as_posix()
            plan["install"] = [f"dotnet restore {rel_project}"]
            plan["build"] = [f"dotnet build {rel_project} --no-restore"]
            plan["test"] = [f"dotnet run --project {rel_project} --no-build -- --self-test"]
            return plan

        return plan

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        pkg_path = repo_root / "package.json"
        if pkg_path.is_file():
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                scripts = pkg.get("scripts", {})
            except (json.JSONDecodeError, OSError):
                scripts = {}

            if "dev" in scripts:
                return {
                    "command": "npm run dev",
                    "port": 5173,
                    "health_path": "/",
                    "timeout_s": 30,
                }
            if "start" in scripts:
                return {
                    "command": "npm start",
                    "port": 3000,
                    "health_path": "/",
                    "timeout_s": 30,
                }

        return {
            "command": None,
            "port": None,
            "health_path": None,
            "timeout_s": None,
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ADAPTERS: dict[str, type] = {
    "react-vite": ReactViteAdapter,
    "nextjs-app": NextJsAdapter,
    "vue-vite": VueViteAdapter,
    "flutter-app": FlutterAppAdapter,
    "expo-react-native": ExpoReactNativeAdapter,
    "node-api": NodeApiAdapter,
    "nestjs-api": NestJsApiAdapter,
    "go-api": GoApiAdapter,
    "dotnet-minimal-api": DotNetMinimalApiAdapter,
    "fastapi-api": FastApiAdapter,
    "django-api": DjangoApiAdapter,
    "flask-api": FlaskApiAdapter,
    "angular": AngularAdapter,
    "spring-boot-api": SpringBootApiAdapter,
    "java-api": JavaApiAdapter,
    "rust-api": RustApiAdapter,
    "agent-selected": AgentSelectedAdapter,
    "generic": GenericAdapter,
    "existing-repo": ExistingRepoAdapter,
}


def get_adapter(profile_id: str) -> StackAdapter:
    """Return the adapter instance for a given profile id.

    Raises ``KeyError`` if no adapter is registered for the id.
    """
    cls = _ADAPTERS.get(profile_id)
    if cls is None:
        raise KeyError(f"no stack adapter registered for profile {profile_id!r}")
    return cls()


# Maturity tier declared to the founder BEFORE they commit to a stack (Wave 1.5):
# proven (production-tested), supported (works, with stated limits), experimental.
# Unlisted adapters default to experimental -- honest under-promising, not silence.
_ADAPTER_MATURITY: dict[str, str] = {
    "react-vite": "proven",
    "nextjs-app": "proven",
    "node-api": "proven",
    "fastapi-api": "proven",
    "vue-vite": "supported",
    "angular": "supported",
    "nestjs-api": "supported",
    "go-api": "supported",
    "dotnet-minimal-api": "supported",
    "django-api": "supported",
    "flask-api": "supported",
    "flutter-app": "supported",
    "expo-react-native": "supported",
    "generic": "supported",
    "existing-repo": "supported",
    "java-api": "experimental",
    "spring-boot-api": "experimental",
    "rust-api": "experimental",
    "agent-selected": "experimental",
}

_DEFAULT_MATURITY = "experimental"
MATURITY_TIERS = ("proven", "supported", "experimental")


def maturity_of(adapter_id: str) -> str:
    """Maturity tier for a stack adapter; unknown ids default to experimental."""
    return _ADAPTER_MATURITY.get(adapter_id, _DEFAULT_MATURITY)


def list_adapters() -> list[dict[str, str]]:
    """Return metadata for every registered adapter, incl. its maturity tier."""
    return [
        {
            "id": adapter_id,
            "display_name": _ADAPTERS[adapter_id]().display_name,
            "maturity": maturity_of(adapter_id),
        }
        for adapter_id in sorted(_ADAPTERS)
    ]


def _profile_from_meta(repo_root: Path) -> str | None:
    """The stack the founder EXPLICITLY selected, read from
    ``.signalos/profile.json``.

    Two schemas exist in the wild: ``commands/init.py`` writes
    ``{"profile_id": "..."}`` while the adapters' :func:`_write_profile_meta`
    writes ``{"profile": "..."}``. BOTH keys are accepted. Returns the selected
    profile id ONLY when it names a known adapter in the registry; returns
    ``None`` when the file is absent, unreadable, malformed, or names an
    unknown profile -- so the caller falls back to on-disk marker detection
    rather than trusting an arbitrary string.
    """
    path = Path(repo_root) / ".signalos" / "profile.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("profile", "profile_id"):
        value = data.get(key)
        if isinstance(value, str) and value.strip() in _ADAPTERS:
            return value.strip()
    return None


def detect_profile(repo_root: Path) -> str:
    """Auto-detect which profile best fits a repository.

    Honors the founder's EXPLICIT stack selection FIRST: a
    ``.signalos/profile.json`` naming a known adapter WINS over on-disk
    inference. This is what lets a greenfield repo -- selected as (say)
    ``react-vite`` but whose stack shell has not been materialized yet -- be
    built with the chosen stack instead of being mis-detected as ``"generic"``
    and handed Python compile/unittest commands.

    Falls back to on-disk marker detection when profile.json is absent or names
    an unknown profile, and to ``"generic"`` when nothing specific is detected.
    """
    root = Path(repo_root)
    selected = _profile_from_meta(root)
    if selected is not None:
        return selected
    return _detect_profile_from_markers(root)


# Profiles whose adapter owns a concrete framework "shell" (build tooling,
# entry points, config files) that GateOrchestrator materializes on a
# greenfield repo BEFORE the build gate -- so G4 does not deadlock trying to
# bootstrap root files governance denies. Excluded: ``generic`` (a bare stdlib
# repo, no framework shell), ``existing-repo`` (preserves the repo as-is), and
# ``agent-selected`` (the agent chooses and creates its own tooling).
_GREENFIELD_SHELL_EXCLUDED = frozenset({"generic", "existing-repo", "agent-selected"})


def adapter_has_greenfield_shell(profile_id: str) -> bool:
    """True iff ``profile_id`` names a registered adapter that materializes a
    concrete framework shell worth pre-scaffolding before a greenfield build."""
    return profile_id in _ADAPTERS and profile_id not in _GREENFIELD_SHELL_EXCLUDED


def stack_shell_present(repo_root: Path) -> bool:
    """True iff the repo already has a recognizable build shell on disk
    (marker files such as package.json / pyproject.toml / go.mod / ...).

    Purely on-disk -- it deliberately IGNORES ``.signalos/profile.json`` and
    answers only "is there already a project shell here that scaffolding could
    overwrite?". This is the idempotency guard for the scaffold-first step: an
    already-scaffolded repo (most importantly one with an existing
    ``package.json``) reports present, so scaffold-first is a strict no-op and
    never touches the existing shell.
    """
    return _detect_profile_from_markers(Path(repo_root)) != "generic"


def _detect_profile_from_markers(root: Path) -> str:
    """Infer the profile purely from on-disk marker files (package.json,
    pyproject.toml, Cargo.toml, go.mod, pom.xml, dotnet markers,
    ``_PROJECT_MARKERS``). The filesystem fallback for :func:`detect_profile`;
    returns ``"generic"`` when nothing specific is detected.
    """
    root = Path(root)

    # Check for vite/react first (most specific)
    pkg_path = root / "package.json"
    if pkg_path.is_file():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "@angular/core" in all_deps or "@angular/cli" in all_deps:
                return "angular"
            if "next" in all_deps:
                return "nextjs-app"
            if "vue" in all_deps:
                return "vue-vite"
            if "expo" in all_deps or "react-native" in all_deps:
                return "expo-react-native"
            if "@nestjs/core" in all_deps:
                return "nestjs-api"
            if "vite" in all_deps:
                return "react-vite"
            if "express" in all_deps:
                return "node-api"
        except (json.JSONDecodeError, OSError):
            pass

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            pyproject_text = pyproject.read_text(encoding="utf-8").lower()
            if "django" in pyproject_text:
                return "django-api"
            if "fastapi" in pyproject_text:
                return "fastapi-api"
            if "flask" in pyproject_text:
                return "flask-api"
        except OSError:
            pass

    if (root / "Cargo.toml").is_file() and (
        (root / "src" / "main.rs").is_file()
        or (root / "src" / "lib.rs").is_file()
    ):
        return "rust-api"

    pubspec = root / "pubspec.yaml"
    if pubspec.is_file():
        try:
            if "flutter" in pubspec.read_text(encoding="utf-8").lower():
                return "flutter-app"
        except OSError:
            pass

    if (root / "go.mod").is_file() and (
        (root / "cmd" / "server" / "main.go").is_file()
        or (root / "internal" / "app" / "app.go").is_file()
    ):
        return "go-api"

    if (
        (root / "pom.xml").is_file()
        or (root / "build.gradle").is_file()
        or (root / "build.gradle.kts").is_file()
    ) and (root / "src" / "main" / "java").is_dir():
        for build_file in ("pom.xml", "build.gradle", "build.gradle.kts"):
            path = root / build_file
            if not path.is_file():
                continue
            try:
                if "spring-boot" in path.read_text(encoding="utf-8").lower():
                    return "spring-boot-api"
            except OSError:
                pass
        return "java-api"

    if _has_dotnet_project(root):
        return "dotnet-minimal-api"

    # Check for any other known project marker -> existing-repo
    for marker_file, _, _ in _PROJECT_MARKERS:
        if (root / marker_file).is_file():
            return "existing-repo"

    return "generic"
