"""Profile-aware stack adapter contract and implementations.

Each adapter knows how to detect, scaffold, resolve targets, plan
validation commands, and plan preview/dev-server configuration for a
particular product stack.
"""

from __future__ import annotations

import json
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
        """Create real scaffold files for this stack.

        Returns manifest of created files.  Must include
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


# ---------------------------------------------------------------------------
# ReactViteAdapter
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
        "@testing-library/jest-dom": "^6.4.0",
        "jsdom": "^24.0.0",
    },
}

_VITE_CONFIG = """\
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
  },
});
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
import React from 'react';

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

_TSCONFIG = {
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
    },
    "include": ["src"],
}


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
        if (repo_root / "vite.config.ts").is_file() or (repo_root / "vite.config.js").is_file():
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
        created: list[str] = []

        def _write(rel: str, content: str) -> None:
            target = repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            created.append(rel)

        pkg = dict(_PACKAGE_JSON_TEMPLATE)
        pkg["dependencies"] = dict(_PACKAGE_JSON_TEMPLATE["dependencies"])
        pkg["devDependencies"] = dict(_PACKAGE_JSON_TEMPLATE["devDependencies"])
        if dependencies:
            pkg["dependencies"].update(dependencies)
        _write("package.json", json.dumps(pkg, indent=2) + "\n")
        _write("vite.config.ts", _VITE_CONFIG)
        _write("index.html", _INDEX_HTML)
        _write("src/main.tsx", _MAIN_TSX)
        _write("src/App.tsx", _APP_TSX)
        _write("src/App.test.tsx", _APP_TEST_TSX)
        _write("tsconfig.json", json.dumps(_TSCONFIG, indent=2) + "\n")

        # Governance directory
        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {"profile": self.id, "display_name": self.display_name}
        (signalos_dir / "profile.json").write_text(
            json.dumps(profile_meta, indent=2) + "\n", encoding="utf-8"
        )
        created.append(".signalos/profile.json")

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
# GenericAdapter
# ---------------------------------------------------------------------------

@dataclass
class GenericAdapter:
    """Conservative adapter for repos without a known stack.

    Cannot claim runnable UI delivery -- higher-level code must treat
    this as a blocker if runnable delivery is required.
    """

    id: str = "generic"
    display_name: str = "Generic Product Repo"

    def detect(self, repo_root: Path) -> dict[str, Any]:
        return {
            "profile": self.id,
            "can_deliver_ui": False,
            "can_deliver_runnable": False,
            "signals": [],
        }

    def scaffold(self, repo_root: Path, intent: dict[str, Any]) -> dict[str, Any]:
        created: list[str] = []

        signalos_dir = repo_root / ".signalos"
        signalos_dir.mkdir(parents=True, exist_ok=True)
        profile_meta = {"profile": self.id, "display_name": self.display_name}
        (signalos_dir / "profile.json").write_text(
            json.dumps(profile_meta, indent=2) + "\n", encoding="utf-8"
        )
        created.append(".signalos/profile.json")

        return {
            "created": created,
            "can_deliver_ui": False,
            "can_deliver_runnable": False,
        }

    def resolve_targets(self, repo_root: Path) -> dict[str, str]:
        return {
            "source": "",
            "tests": "",
            "config": "",
            "public": "",
        }

    def validation_plan(self, repo_root: Path) -> dict[str, list[str]]:
        return _empty_validation_plan()

    def preview_plan(self, repo_root: Path) -> dict[str, Any]:
        return {
            "command": None,
            "port": None,
            "health_path": None,
            "timeout_s": None,
        }


# ---------------------------------------------------------------------------
# ExistingRepoAdapter
# ---------------------------------------------------------------------------

# Detectable project markers and associated profile hints
_PROJECT_MARKERS: list[tuple[str, str, str]] = [
    # (filename, dependency-key-or-"*", profile-hint)
    ("package.json", "vite", "react-vite"),
    ("package.json", "react", "react-vite"),
    ("package.json", "*", "node"),
    ("Cargo.toml", "*", "rust"),
    ("pyproject.toml", "*", "python"),
    ("go.mod", "*", "go"),
    ("pom.xml", "*", "java-maven"),
    ("build.gradle", "*", "java-gradle"),
    ("build.gradle.kts", "*", "java-gradle"),
]


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

        has_src = (repo_root / "src").is_dir()
        if has_src:
            signals.append("src-dir")

        can_ui = "react-vite" in detected_stacks
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
        (signalos_dir / "profile.json").write_text(
            json.dumps(profile_meta, indent=2) + "\n", encoding="utf-8"
        )
        created.append(".signalos/profile.json")

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


def list_adapters() -> list[dict[str, str]]:
    """Return metadata for every registered adapter."""
    return [
        {"id": adapter_id, "display_name": _ADAPTERS[adapter_id]().display_name}
        for adapter_id in sorted(_ADAPTERS)
    ]


def detect_profile(repo_root: Path) -> str:
    """Auto-detect which profile best fits a repository.

    Returns the profile id string.  Falls back to ``"generic"`` when
    nothing specific is detected.
    """
    root = Path(repo_root)

    # Check for vite/react first (most specific)
    pkg_path = root / "package.json"
    if pkg_path.is_file():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "vite" in all_deps:
                return "react-vite"
        except (json.JSONDecodeError, OSError):
            pass

    # Check for any other known project marker -> existing-repo
    for marker_file, _, _ in _PROJECT_MARKERS:
        if (root / marker_file).is_file():
            return "existing-repo"

    return "generic"
