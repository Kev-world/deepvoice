"""Sub-agent for static repository analysis (structure, dependencies, entry points)."""

import json
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# File extensions mapped to programming languages.
_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript (React)",
    ".jsx": "JavaScript (React)",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".h": "C/C++ Header",
    ".swift": "Swift",
    ".scala": "Scala",
    ".r": "R",
    ".R": "R",
    ".lua": "Lua",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".sql": "SQL",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hs": "Haskell",
    ".ml": "OCaml",
    ".proto": "Protobuf",
}

# Directories to always skip during traversal.
_SKIP_DIRS: set[str] = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "venv",
    "env",
    ".env",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
    "vendor",
    ".cargo",
}

# Dependency manifest filenames and their ecosystems.
_DEPENDENCY_FILES: dict[str, str] = {
    "package.json": "Node.js (npm/yarn)",
    "pyproject.toml": "Python (PEP 621 / Poetry)",
    "requirements.txt": "Python (pip)",
    "setup.py": "Python (setuptools)",
    "setup.cfg": "Python (setuptools)",
    "Pipfile": "Python (Pipenv)",
    "go.mod": "Go",
    "Cargo.toml": "Rust (Cargo)",
    "Gemfile": "Ruby (Bundler)",
    "composer.json": "PHP (Composer)",
    "pom.xml": "Java (Maven)",
    "build.gradle": "Java/Kotlin (Gradle)",
    "build.gradle.kts": "Kotlin (Gradle KTS)",
    "pubspec.yaml": "Dart (Pub)",
    "mix.exs": "Elixir (Mix)",
    "Makefile": "Make",
    "CMakeLists.txt": "C/C++ (CMake)",
}

# Framework indicator files/dirs and their descriptions.
_FRAMEWORK_INDICATORS: dict[str, str] = {
    "next.config.js": "Next.js",
    "next.config.mjs": "Next.js",
    "next.config.ts": "Next.js",
    "nuxt.config.js": "Nuxt.js",
    "nuxt.config.ts": "Nuxt.js",
    "angular.json": "Angular",
    "svelte.config.js": "SvelteKit",
    "astro.config.mjs": "Astro",
    "vite.config.ts": "Vite",
    "vite.config.js": "Vite",
    "webpack.config.js": "Webpack",
    "tailwind.config.js": "Tailwind CSS",
    "tailwind.config.ts": "Tailwind CSS",
    "tsconfig.json": "TypeScript",
    "Dockerfile": "Docker",
    "docker-compose.yml": "Docker Compose",
    "docker-compose.yaml": "Docker Compose",
    ".github": "GitHub Actions",
    "Procfile": "Heroku",
    "serverless.yml": "Serverless Framework",
    "terraform": "Terraform",
    "Makefile": "Make",
}

# Common entry point filenames.
_ENTRY_POINT_NAMES: set[str] = {
    "main.py",
    "app.py",
    "manage.py",
    "wsgi.py",
    "asgi.py",
    "server.py",
    "index.js",
    "index.ts",
    "server.js",
    "server.ts",
    "app.js",
    "app.ts",
    "main.js",
    "main.ts",
    "main.go",
    "cmd",
    "main.rs",
    "lib.rs",
    "Main.java",
    "Application.java",
    "index.html",
}


class RepoAnalyzer:
    """Analyses a repository's structure, languages, dependencies, and entry points."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_structure(self, repo_path: str) -> dict[str, Any]:
        """Analyse the high-level structure of a repository.

        Args:
            repo_path: Path to the repository root.

        Returns:
            A dict with keys:
                - languages: Mapping of language name to file count.
                - frameworks: List of detected framework/tooling names.
                - key_directories: List of notable top-level directories.
                - total_files: Total number of source files found.
        """
        root = Path(repo_path).resolve()
        if not root.is_dir():
            logger.error("Repository path does not exist: %s", root)
            return {
                "languages": {},
                "frameworks": [],
                "key_directories": [],
                "total_files": 0,
            }

        logger.info("Analysing repository structure: %s", root)

        language_counts: Counter[str] = Counter()
        total_files = 0

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune directories we never want to traverse.
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

            for filename in filenames:
                ext = os.path.splitext(filename)[1]
                lang = _EXTENSION_TO_LANGUAGE.get(ext)
                if lang:
                    language_counts[lang] += 1
                    total_files += 1

        frameworks = self._detect_frameworks(root)
        key_dirs = self._find_key_directories(root)

        result: dict[str, Any] = {
            "languages": dict(language_counts.most_common()),
            "frameworks": frameworks,
            "key_directories": key_dirs,
            "total_files": total_files,
        }

        logger.info(
            "Structure analysis complete: %d files, %d languages, %d frameworks.",
            total_files,
            len(language_counts),
            len(frameworks),
        )
        return result

    def analyze_dependencies(self, repo_path: str) -> dict[str, Any]:
        """Read and summarise dependency manifest files.

        Args:
            repo_path: Path to the repository root.

        Returns:
            A dict mapping each found manifest filename to a sub-dict with:
                - ecosystem: The package ecosystem description.
                - path: Absolute path to the manifest.
                - details: Parsed dependency names (best-effort).
        """
        root = Path(repo_path).resolve()
        if not root.is_dir():
            logger.error("Repository path does not exist: %s", root)
            return {}

        logger.info("Analysing dependencies for: %s", root)

        results: dict[str, Any] = {}

        for manifest_name, ecosystem in _DEPENDENCY_FILES.items():
            manifest_path = root / manifest_name
            if manifest_path.is_file():
                details = self._parse_manifest(manifest_path, manifest_name)
                results[manifest_name] = {
                    "ecosystem": ecosystem,
                    "path": str(manifest_path),
                    "details": details,
                }
                logger.debug("Found %s (%s).", manifest_name, ecosystem)

        logger.info("Dependency analysis found %d manifest(s).", len(results))
        return results

    def find_entry_points(self, repo_path: str) -> list[str]:
        """Locate likely application entry points.

        Args:
            repo_path: Path to the repository root.

        Returns:
            A list of relative paths (from repo root) to probable entry points.
        """
        root = Path(repo_path).resolve()
        if not root.is_dir():
            logger.error("Repository path does not exist: %s", root)
            return []

        logger.info("Searching for entry points in: %s", root)

        entry_points: list[str] = []

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

            for filename in filenames:
                if filename in _ENTRY_POINT_NAMES:
                    full_path = Path(dirpath) / filename
                    relative = str(full_path.relative_to(root))
                    entry_points.append(relative)

            # Also check for 'cmd' directory typical in Go projects.
            if "cmd" in dirnames:
                cmd_dir = Path(dirpath) / "cmd"
                for child in cmd_dir.iterdir():
                    if child.is_dir():
                        main_go = child / "main.go"
                        if main_go.exists():
                            entry_points.append(
                                str(main_go.relative_to(root))
                            )

        logger.info("Found %d entry point(s).", len(entry_points))
        return sorted(set(entry_points))

    def generate_summary(self, repo_path: str) -> str:
        """Generate a plain-text summary of the repository.

        The summary is designed to be ingested into the RAG knowledge base so
        that the voice agent can answer high-level questions about the project.

        Args:
            repo_path: Path to the repository root.

        Returns:
            A multi-line text summary.
        """
        root = Path(repo_path).resolve()
        repo_name = root.name

        logger.info("Generating summary for repository: %s", root)

        structure = self.analyze_structure(repo_path)
        dependencies = self.analyze_dependencies(repo_path)
        entry_points = self.find_entry_points(repo_path)

        lines: list[str] = [
            f"Repository: {repo_name}",
            f"Path: {root}",
            "",
        ]

        # Languages
        if structure["languages"]:
            lines.append("Languages:")
            for lang, count in structure["languages"].items():
                lines.append(f"  - {lang}: {count} file(s)")
            lines.append("")

        # Frameworks / tooling
        if structure["frameworks"]:
            lines.append("Frameworks & Tooling:")
            for fw in structure["frameworks"]:
                lines.append(f"  - {fw}")
            lines.append("")

        # Key directories
        if structure["key_directories"]:
            lines.append("Key Directories:")
            for d in structure["key_directories"]:
                lines.append(f"  - {d}/")
            lines.append("")

        # Dependencies
        if dependencies:
            lines.append("Dependency Manifests:")
            for manifest, info in dependencies.items():
                dep_names = info.get("details", {}).get("dependencies", [])
                dep_count = len(dep_names) if isinstance(dep_names, list) else 0
                lines.append(
                    f"  - {manifest} ({info['ecosystem']}, {dep_count} dependencies)"
                )
            lines.append("")

        # Entry points
        if entry_points:
            lines.append("Entry Points:")
            for ep in entry_points:
                lines.append(f"  - {ep}")
            lines.append("")

        lines.append(
            f"Total source files: {structure['total_files']}"
        )

        summary = "\n".join(lines)

        logger.info(
            "Summary generated for '%s' (%d characters).",
            repo_name,
            len(summary),
        )
        return summary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_frameworks(root: Path) -> list[str]:
        """Detect frameworks and tooling from indicator files."""
        found: list[str] = []
        for indicator, framework in _FRAMEWORK_INDICATORS.items():
            if (root / indicator).exists():
                if framework not in found:
                    found.append(framework)
        return found

    @staticmethod
    def _find_key_directories(root: Path) -> list[str]:
        """Return notable top-level directories (excluding hidden/vendor)."""
        key_dirs: list[str] = []
        try:
            for child in sorted(root.iterdir()):
                if child.is_dir() and child.name not in _SKIP_DIRS and not child.name.startswith("."):
                    key_dirs.append(child.name)
        except PermissionError:
            logger.warning("Permission denied reading: %s", root)
        return key_dirs

    @staticmethod
    def _parse_manifest(path: Path, name: str) -> dict[str, Any]:
        """Best-effort extraction of dependency names from a manifest file.

        Returns a dict that always has a ``dependencies`` key (list of strings).
        """
        result: dict[str, Any] = {"dependencies": []}

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("Could not read manifest: %s", path)
            return result

        if name == "package.json":
            result = RepoAnalyzer._parse_package_json(content)
        elif name == "requirements.txt":
            result = RepoAnalyzer._parse_requirements_txt(content)
        elif name == "pyproject.toml":
            result = RepoAnalyzer._parse_pyproject_toml(content)
        elif name == "go.mod":
            result = RepoAnalyzer._parse_go_mod(content)
        elif name == "Cargo.toml":
            result = RepoAnalyzer._parse_cargo_toml(content)
        else:
            # For unsupported manifest types, return an empty list.
            pass

        return result

    @staticmethod
    def _parse_package_json(content: str) -> dict[str, Any]:
        """Extract dependency names from package.json."""
        deps: list[str] = []
        try:
            data = json.loads(content)
            for section in ("dependencies", "devDependencies", "peerDependencies"):
                if section in data and isinstance(data[section], dict):
                    deps.extend(data[section].keys())
        except (json.JSONDecodeError, KeyError):
            logger.debug("Failed to parse package.json.")
        return {"dependencies": sorted(set(deps))}

    @staticmethod
    def _parse_requirements_txt(content: str) -> dict[str, Any]:
        """Extract package names from requirements.txt."""
        deps: list[str] = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Strip version specifiers.
            for sep in ("==", ">=", "<=", "!=", "~=", ">", "<", "["):
                idx = line.find(sep)
                if idx != -1:
                    line = line[:idx]
                    break
            pkg = line.strip()
            if pkg:
                deps.append(pkg)
        return {"dependencies": sorted(set(deps))}

    @staticmethod
    def _parse_pyproject_toml(content: str) -> dict[str, Any]:
        """Extract dependency names from pyproject.toml (best effort, regex-based)."""
        import re

        deps: list[str] = []
        # Match lines like: "requests>=2.0", "httpx", etc. inside dependencies arrays.
        in_deps = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("dependencies") and "=" in stripped:
                in_deps = True
                continue
            if in_deps:
                if stripped == "]":
                    in_deps = False
                    continue
                # Extract quoted package name.
                match = re.match(r'^"([a-zA-Z0-9_\-]+)', stripped)
                if match:
                    deps.append(match.group(1))
        return {"dependencies": sorted(set(deps))}

    @staticmethod
    def _parse_go_mod(content: str) -> dict[str, Any]:
        """Extract module paths from go.mod."""
        deps: list[str] = []
        in_require = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("require ("):
                in_require = True
                continue
            if in_require:
                if stripped == ")":
                    in_require = False
                    continue
                parts = stripped.split()
                if parts:
                    deps.append(parts[0])
            elif stripped.startswith("require "):
                parts = stripped.split()
                if len(parts) >= 2:
                    deps.append(parts[1])
        return {"dependencies": sorted(set(deps))}

    @staticmethod
    def _parse_cargo_toml(content: str) -> dict[str, Any]:
        """Extract crate names from Cargo.toml (best effort)."""
        import re

        deps: list[str] = []
        in_deps = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped in ("[dependencies]", "[dev-dependencies]", "[build-dependencies]"):
                in_deps = True
                continue
            if stripped.startswith("[") and in_deps:
                in_deps = False
                continue
            if in_deps and "=" in stripped:
                match = re.match(r'^([a-zA-Z0-9_\-]+)\s*=', stripped)
                if match:
                    deps.append(match.group(1))
        return {"dependencies": sorted(set(deps))}
