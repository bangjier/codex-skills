#!/usr/bin/env python3

import datetime as dt
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN_ROOT / "scripts" / "publish_version_readme.py"


def run(args, cwd, check=True):
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONPYCACHEPREFIX": str(Path(tempfile.gettempdir()) / "release-readme-test-pycache"),
        }
    )
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(
            "command failed: {}\nstdout:\n{}\nstderr:\n{}".format(
                args, result.stdout, result.stderr
            )
        )
    return result


class TempRepo:
    def __init__(self, root):
        self.root = Path(root)
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args, check=True):
        return run(["git", *args], self.root, check=check)

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def cli(self, *args, check=False):
        result = run([sys.executable, str(SCRIPT), *args, "--repo", str(self.root)], self.root, check=False)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                "non-JSON output\nstdout:\n{}\nstderr:\n{}".format(
                    result.stdout, result.stderr
                )
            ) from exc
        if check and result.returncode:
            raise AssertionError(payload)
        return result, payload

    def stage(self, version, *checks, message="更新示例文件并补充发布说明", check=False):
        args = ["stage", "--expected-version", version]
        if message is not None:
            args.extend(["--commit-message", message])
        for command in checks:
            args.extend(["--check-command", command])
        return self.cli(*args, check=check)

    def publish_plan(self, stage_payload, check=False):
        return self.cli(
            "publish",
            "--plan-token",
            stage_payload["plan_token"],
            check=check,
        )


def custom_config(extra=""):
    return (
        "version:\n"
        "  provider: custom\n"
        "  file: version.json\n"
        "  field: release.version\n"
        "  build_field: release.build\n"
        "checks:\n"
        "  commands: []\n"
        + extra
    )


def initialize_custom(repo, committed_current=True, current="1.1.0"):
    repo.write(".release-readme.yaml", custom_config())
    repo.write("version.json", '{"release":{"version":"0.9.0","build":9}}\n')
    repo.write("README.md", "# Demo\n\n## Release Notes\n\n")
    repo.write("modified.txt", "base\n")
    repo.write("staged.txt", "base\n")
    repo.write("deleted.txt", "base\n")
    repo.write(".gitignore", "ignored.log\n")
    repo.commit("initial version")
    repo.write("version.json", '{"release":{"version":"1.0.0","build":10}}\n')
    boundary = repo.commit("release 1.0.0")
    repo.write("feature.txt", "feature\n")
    repo.commit("add feature")
    repo.write(
        "version.json",
        json.dumps({"release": {"version": current, "build": 11}}) + "\n",
    )
    if committed_current:
        repo.commit("bump current version")
    return boundary


def flutter_pubspec(version):
    return "name: demo\nversion: {}\nenvironment:\n  sdk: '>=2.17.0 <4.0.0'\n".format(version)


def ios_pbxproj(two_targets=False):
    second = ""
    if two_targets:
        second = """
        999999999999999999999999 /* Other */ = {
            isa = PBXNativeTarget;
            name = Other;
            productType = "com.apple.product-type.application";
            buildConfigurationList = 888888888888888888888888;
        };
        888888888888888888888888 /* Build configuration list for Other */ = {
            isa = XCConfigurationList;
            buildConfigurations = (
                222222222222222222222222,
            );
        };
"""
    return """// !$*UTF8*$!
{
    objects = {
        AAAAAAAAAAAAAAAAAAAAAAAA /* Versions.xcconfig */ = {
            isa = PBXFileReference;
            path = Config/Versions.xcconfig;
        };
        111111111111111111111111 /* Debug */ = {
            isa = XCBuildConfiguration;
            baseConfigurationReference = AAAAAAAAAAAAAAAAAAAAAAAA /* Versions.xcconfig */;
            buildSettings = {
                MARKETING_VERSION = $(APP_MARKETING_VERSION);
                CURRENT_PROJECT_VERSION = $(APP_BUILD_NUMBER);
            };
            name = Debug;
        };
        222222222222222222222222 /* Release */ = {
            isa = XCBuildConfiguration;
            baseConfigurationReference = AAAAAAAAAAAAAAAAAAAAAAAA /* Versions.xcconfig */;
            buildSettings = {
                MARKETING_VERSION = $(APP_MARKETING_VERSION);
                CURRENT_PROJECT_VERSION = $(APP_BUILD_NUMBER);
            };
            name = Release;
        };
        333333333333333333333333 /* Build configuration list for Runner */ = {
            isa = XCConfigurationList;
            buildConfigurations = (
                111111111111111111111111,
                222222222222222222222222,
            );
        };
        444444444444444444444444 /* Runner */ = {
            isa = PBXNativeTarget;
            name = Runner;
            productType = "com.apple.product-type.application";
            buildConfigurationList = 333333333333333333333333;
        };
%s
    };
}
""" % second


class PublishVersionReadmeTests(unittest.TestCase):
    def make_repo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return TempRepo(temp.name)

    def make_bare(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "remote.git"
        run(["git", "init", "--bare", "--initial-branch=main", str(path)], Path(temp.name))
        return path

    def test_flutter_uncommitted_boundary_and_all_status_kinds(self):
        repo = self.make_repo()
        repo.write("pubspec.yaml", flutter_pubspec("0.9.0+9"))
        repo.write("README.md", "# Flutter Demo\n")
        repo.write("modified.txt", "base\n")
        repo.write("staged.txt", "base\n")
        repo.write("deleted.txt", "base\n")
        repo.write(".gitignore", "ignored.log\nvendor/\n")
        repo.commit("initial")
        repo.write("pubspec.yaml", flutter_pubspec("1.0.0+10"))
        boundary = repo.commit("release 1.0")
        repo.write("feature.dart", "feature\n")
        repo.commit("add feature")
        repo.write("pubspec.yaml", flutter_pubspec("1.1.0+11"))
        repo.write("modified.txt", "modified\n")
        repo.write("staged.txt", "staged\n")
        repo.git("add", "staged.txt")
        repo.write("untracked.txt", "new\n")
        (repo.root / "deleted.txt").unlink()
        repo.write("ignored.log", "ignored\n")
        repo.write("vendor/pubspec.yaml", flutter_pubspec("9.9.9+999"))

        result, payload = repo.cli("inspect", "--mode", "preview", check=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["version"]["provider"], "flutter")
        self.assertEqual(payload["version"]["version"], "1.1.0+11")
        self.assertEqual(payload["boundary"]["commit"], boundary)
        self.assertTrue(payload["version_uncommitted"])
        self.assertIn("staged.txt", payload["status"]["staged"])
        self.assertIn("modified.txt", payload["status"]["unstaged"])
        self.assertIn("untracked.txt", payload["status"]["untracked"])
        self.assertIn("deleted.txt", payload["status"]["deleted"])
        self.assertNotIn("ignored.log", json.dumps(payload))
        self.assertNotIn("vendor/pubspec.yaml", json.dumps(payload))

    def test_flutter_multiple_versioned_modules_require_configuration(self):
        repo = self.make_repo()
        repo.write("pubspec.yaml", flutter_pubspec("1.0.0+1"))
        repo.write("packages/child/pubspec.yaml", flutter_pubspec("2.0.0+2"))
        repo.write("README.md", "# Flutter workspace\n")
        repo.commit("initial")
        result, payload = repo.cli("inspect", "--mode", "preview")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["error"]["code"], "ambiguous_version")

        cross_provider = self.make_repo()
        cross_provider.write("client/pubspec.yaml", flutter_pubspec("1.0.0+1"))
        cross_provider.write("App.xcodeproj/project.pbxproj", ios_pbxproj())
        cross_provider.write("README.md", "# Multi-platform workspace\n")
        cross_provider.commit("initial")
        result, payload = cross_provider.cli("inspect", "--mode", "preview")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["error"]["code"], "ambiguous_version")

    def test_current_version_summary_replaces_old_body_and_is_idempotent(self):
        repo = self.make_repo()
        boundary = initialize_custom(repo, committed_current=True)
        repo.write(
            ".release-readme.yaml",
            custom_config()
            + "readme:\n"
            + "  section: Release Notes\n",
        )
        prefix = "# Demo\n\n## Release Notes\n\n"
        history = "### 1.0.0 - 2025-01-01\n\n- Older item\n\n## Usage\n\nKeep these instructions.\n"
        repo.write(
            "README.md",
            prefix + "### v1.1.0 - 2026-01-01\n\n"
            "Replaced release context.\n\n- Add login\n- Fix login errors\n\n"
            "#### Experimental feature\n\n- Reverted experiment\n\n" + history,
        )
        notes = repo.write(
            "notes.json",
            json.dumps(
                {
                    "version": "1.1.0",
                    "date": dt.date.today().isoformat(),
                    "section_heading": "Wrong Region",
                    "items": ["Add login with clear error messages", "Add login with clear error messages."],
                }
            ),
        )
        inspect_result, inspect_payload = repo.cli("inspect", "--mode", "preview", check=True)
        self.assertEqual(inspect_result.returncode, 0)
        self.assertEqual(inspect_payload["boundary"]["commit"], boundary)
        self.assertFalse(inspect_payload["version_uncommitted"])
        before = (repo.root / "README.md").read_text(encoding="utf-8")
        preview, preview_payload = repo.cli(
            "render", "--notes-file", str(notes), "--preview", check=True
        )
        self.assertEqual(preview.returncode, 0)
        self.assertIn("+- Add login with clear error messages", preview_payload["diff"])
        self.assertIn("-- Fix login errors", preview_payload["diff"])
        self.assertEqual((repo.root / "README.md").read_text(encoding="utf-8"), before)
        repo.cli("render", "--notes-file", str(notes), check=True)
        rendered = (repo.root / "README.md").read_text(encoding="utf-8")
        self.assertEqual(rendered.count("### v1.1.0"), 1)
        self.assertEqual(rendered.count("Add login with clear error messages"), 1)
        self.assertNotIn("Fix login errors", rendered)
        self.assertNotIn("Replaced release context.", rendered)
        self.assertNotIn("Experimental feature", rendered)
        self.assertNotIn("Reverted experiment", rendered)
        self.assertTrue(rendered.startswith(prefix))
        self.assertTrue(rendered.endswith(history))
        self.assertNotIn("Wrong Region", rendered)
        self.assertIn(dt.date.today().isoformat(), rendered)
        _, repeated = repo.cli("render", "--notes-file", str(notes), check=True)
        self.assertFalse(repeated["changed"])
        self.assertEqual((repo.root / "README.md").read_text(encoding="utf-8"), rendered)

    def test_existing_chinese_heading_and_contiguous_bullets_are_preserved(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        repo.write(
            "README.md",
            "# Demo\n\n## 版本更新记录\n\n"
            "### 1.1.0（2026-01-01）\n\n"
            "- 现有条目\n\n"
            "### 1.0.0（2025-01-01）\n\n- 旧条目\n",
        )
        notes = repo.write(
            "notes.json",
            json.dumps(
                {
                    "version": "1.1.0",
                    "date": dt.date.today().isoformat(),
                    "section_heading": "版本更新记录",
                    "items": ["整合后的功能摘要", "独立的重要修复"],
                }
            ),
        )

        repo.cli("render", "--notes-file", str(notes), check=True)
        rendered = (repo.root / "README.md").read_text(encoding="utf-8")

        self.assertIn(f"### 1.1.0（{dt.date.today().isoformat()}）", rendered)
        self.assertNotIn("### 1.1.0 -", rendered)
        self.assertIn("- 整合后的功能摘要\n- 独立的重要修复", rendered)
        self.assertNotIn("现有条目", rendered)
        self.assertIn("### 1.0.0（2025-01-01）\n\n- 旧条目\n", rendered)

    def test_new_version_preserves_existing_release_history(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        prefix = "# Demo\n\n## Release Notes\n\nThese are user-facing changes.\n\n"
        history = (
            "### 1.0.0 - 2026-01-01\n\n* Previous feature\n\n"
            "#### Migration\n\nKeep this migration guide.\n\n"
            "### 0.9.0 - 2025-01-01\n\n* Earlier feature\n\n"
            "## Usage\n\nKeep these instructions.\n"
        )
        repo.write("README.md", prefix + history)
        items = [f"Independent change {index}" for index in range(6)]
        notes = repo.write(
            "notes.json",
            json.dumps({"version": "1.1.0", "date": dt.date.today().isoformat(), "items": items}),
        )
        repo.cli("render", "--notes-file", str(notes), check=True)
        rendered = (repo.root / "README.md").read_text(encoding="utf-8")
        self.assertTrue(rendered.startswith(prefix + "### 1.1.0 - "))
        self.assertTrue(rendered.endswith(history))
        for item in items:
            self.assertIn("* " + item + "\n", rendered)
        _, repeated = repo.cli("render", "--notes-file", str(notes), check=True)
        self.assertFalse(repeated["changed"])
        self.assertEqual((repo.root / "README.md").read_text(encoding="utf-8"), rendered)

    def test_summary_replacement_respects_markers_and_line_endings(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        repo.write(
            ".release-readme.yaml",
            custom_config(
                "readme:\n"
                "  start_marker: '<!-- release-notes:start -->'\n"
                "  end_marker: '<!-- release-notes:end -->'\n"
            ),
        )
        prefix = "# Demo\r\n\r\n<!-- release-notes:start -->\r\n\r\n"
        suffix = (
            "### 1.0.0 - 2025-01-01\r\n\r\n* Older item\r\n\r\n"
            "<!-- release-notes:end -->\r\n\r\n"
            "## Compatibility with 1.1.0\r\n\r\nKeep these instructions.\r\n"
        )
        readme = repo.root / "README.md"
        readme.write_bytes((prefix + "### v1.1.0（2026-01-01）\r\n\r\n* Old item\r\n\r\n" + suffix).encode("utf-8"))
        notes = repo.write(
            "notes.json",
            json.dumps({"version": "1.1.0", "date": dt.date.today().isoformat(), "items": ["Consolidated summary"]}),
        )
        repo.cli("render", "--notes-file", str(notes), check=True)
        rendered = readme.read_bytes().decode("utf-8")
        self.assertTrue(rendered.startswith(prefix))
        self.assertTrue(rendered.endswith(suffix))
        self.assertIn(f"### v1.1.0（{dt.date.today().isoformat()}）\r\n\r\n* Consolidated summary\r\n", rendered)
        self.assertNotIn("Old item", rendered)
        self.assertNotIn("\n", rendered.replace("\r\n", ""))

    def test_empty_summary_does_not_erase_existing_version(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        readme = repo.write("README.md", "# Demo\n\n## Release Notes\n\n### 1.1.0\n\n- Existing feature\n")
        before = readme.read_bytes()
        for items in ([], [" ", "-", "!!!"]):
            with self.subTest(items=items):
                notes = repo.write(
                    "notes.json",
                    json.dumps({"version": "1.1.0", "date": dt.date.today().isoformat(), "items": items}),
                )
                result, payload = repo.cli("render", "--notes-file", str(notes))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(payload["error"]["code"], "invalid_notes")
                self.assertEqual(readme.read_bytes(), before)

    def test_ios_variables_xcconfig_and_target_selection(self):
        repo = self.make_repo()
        repo.write(
            ".release-readme.yaml",
            "version:\n  provider: ios\n  target: Runner\nchecks:\n  commands: []\n",
        )
        repo.write("App.xcodeproj/project.pbxproj", ios_pbxproj(two_targets=True))
        repo.write("Config/Versions.xcconfig", "APP_MARKETING_VERSION = 0.9.0\nAPP_BUILD_NUMBER = 9\n")
        repo.write("README.md", "# iOS\n")
        repo.commit("initial")
        repo.write("Config/Versions.xcconfig", "APP_MARKETING_VERSION = 1.0.0\nAPP_BUILD_NUMBER = 10\n")
        boundary = repo.commit("release 1.0")
        repo.write("ios.txt", "feature\n")
        repo.commit("feature")
        repo.write("Config/Versions.xcconfig", "APP_MARKETING_VERSION = 1.1.0\nAPP_BUILD_NUMBER = 11\n")

        result, payload = repo.cli("inspect", "--mode", "preview", check=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["version"]["provider"], "ios")
        self.assertEqual(payload["version"]["version"], "1.1.0")
        self.assertEqual(payload["version"]["build"], "11")
        self.assertEqual(payload["version"]["details"]["target"], "Runner")
        self.assertEqual(payload["boundary"]["commit"], boundary)

    def test_android_groovy_and_kotlin_dsl(self):
        fixtures = {
            "groovy": (
                "app/build.gradle",
                "apply plugin: 'com.android.application'\n"
                "android {\n"
                "  defaultConfig {\n"
                "    versionName \"__VERSION__\"\n"
                "    versionCode __CODE__\n"
                "  }\n"
                "}\n",
            ),
            "kotlin": (
                "app/build.gradle.kts",
                'plugins { id("com.android.application") }\n'
                'val appVersionName = "__VERSION__"\n'
                "val appVersionCode = __CODE__\n"
                "android {\n"
                "  defaultConfig {\n"
                "    versionName = appVersionName\n"
                "    versionCode = appVersionCode\n"
                "  }\n"
                "}\n",
            ),
        }
        for name, (path, template) in fixtures.items():
            with self.subTest(name=name):
                repo = self.make_repo()
                repo.write(
                    "build.gradle.kts",
                    'plugins { id("com.android.application") version "8.5.0" apply false }\n',
                )
                repo.write(
                    path,
                    template.replace("__VERSION__", "0.9.0").replace("__CODE__", "9"),
                )
                repo.write("README.md", "# Android\n")
                repo.commit("initial")
                repo.write(
                    path,
                    template.replace("__VERSION__", "1.0.0").replace("__CODE__", "10"),
                )
                boundary = repo.commit("release 1.0")
                repo.write("android.txt", "feature\n")
                repo.commit("feature")
                repo.write(
                    path,
                    template.replace("__VERSION__", "1.1.0").replace("__CODE__", "11"),
                )
                result, payload = repo.cli("inspect", "--mode", "preview", check=True)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(payload["version"]["provider"], "android")
                self.assertEqual(payload["version"]["version"], "1.1.0")
                self.assertEqual(payload["version"]["build"], "11")
                self.assertEqual(payload["boundary"]["commit"], boundary)

    def test_android_multiple_modules_stop_without_configuration(self):
        repo = self.make_repo()
        groovy = (
            "apply plugin: 'com.android.application'\n"
            "android { defaultConfig { versionName \"0.9.0\"; versionCode 9 } }\n"
        )
        repo.write("app/build.gradle", groovy)
        repo.write("other/build.gradle", groovy)
        repo.write("README.md", "# Multi\n")
        repo.commit("initial")
        result, payload = repo.cli("inspect", "--mode", "preview")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["error"]["code"], "ambiguous_version")

    def test_custom_regex_provider(self):
        repo = self.make_repo()
        repo.write(
            ".release-readme.yaml",
            "version:\n"
            "  provider: custom\n"
            "  file: VERSION.txt\n"
            "  pattern: 'VERSION=(?P<version>[^ ]+) BUILD=(?P<build>[0-9]+)'\n"
            "checks:\n  commands: []\n",
        )
        repo.write("VERSION.txt", "VERSION=0.9.0 BUILD=9\n")
        repo.write("README.md", "# Custom\n")
        repo.commit("initial")
        repo.write("VERSION.txt", "VERSION=1.0.0 BUILD=10\n")
        boundary = repo.commit("release 1.0")
        repo.write("work.txt", "feature\n")
        repo.commit("feature")
        repo.write("VERSION.txt", "VERSION=1.1.0 BUILD=11\n")
        result, payload = repo.cli("inspect", "--mode", "preview", check=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["version"]["version"], "1.1.0")
        self.assertEqual(payload["version"]["build"], "11")
        self.assertEqual(payload["boundary"]["commit"], boundary)

    def test_code_token_expressions_are_not_sensitive(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        repo.write(
            "cancel_tokens.dart",
            "_pollCancelToken = CancelToken();\n"
            "_installmentIndexCancelToken = CancelToken();\n"
            "cancelToken: cancelToken,\n"
            "cancelToken: _pollCancelToken,\n"
            "cancelToken: CancelToken(),\n"
            "dzb.token: SkdGlobal.getToken(),\n",
        )

        result, payload = repo.cli("inspect", "--mode", "preview", check=True)

        self.assertEqual(result.returncode, 0)
        self.assertTrue(payload["ok"])

    def test_literal_secret_assignments_remain_sensitive(self):
        cases = (
            "TOKEN=not-a-real-token-but-long\n",
            "access_token=abcdefghijklmnopqrstuvwxyz123456\n",
            "PASSWORD=not!a$real%password123\n",
            'apiToken: "not-a-real-hardcoded-token",\n',
            "apiKey = 'not-a-real-hardcoded-value';\n",
        )
        for content in cases:
            with self.subTest(content=content.split("=", 1)[0].split(":", 1)[0]):
                repo = self.make_repo()
                initialize_custom(repo, committed_current=True)
                repo.write("candidate.txt", content)

                result, payload = repo.cli("inspect", "--mode", "preview")

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(payload["error"]["code"], "sensitive_content")

    def test_sensitive_file_detached_head_merge_and_no_remote_block(self):
        cases = (
            "sensitive",
            "intermediate_secret",
            "secret_subject",
            "detached",
            "merge",
            "no_remote",
        )
        for case in cases:
            with self.subTest(case=case):
                repo = self.make_repo()
                initialize_custom(repo, committed_current=True)
                if case == "sensitive":
                    repo.write(".env", "TOKEN=not-a-real-token-but-long\n")
                    mode = "preview"
                    expected = "sensitive_content"
                elif case == "intermediate_secret":
                    repo.write(
                        "temporary.txt",
                        "access_token=abcdefghijklmnopqrstuvwxyz123456\n",
                    )
                    repo.commit("temporarily add credential")
                    (repo.root / "temporary.txt").unlink()
                    repo.commit("remove credential")
                    mode = "preview"
                    expected = "sensitive_content"
                elif case == "secret_subject":
                    repo.write("metadata.txt", "metadata only\n")
                    repo.commit("OPENAI_TOKEN=sk-proj-abcdefghijklmnopqrstuvwxyz123456")
                    mode = "preview"
                    expected = "sensitive_content"
                elif case == "detached":
                    repo.git("checkout", "--detach", "HEAD")
                    mode = "preview"
                    expected = "detached_head"
                elif case == "merge":
                    head = repo.git("rev-parse", "HEAD").stdout
                    (repo.root / ".git" / "MERGE_HEAD").write_text(head, encoding="ascii")
                    mode = "preview"
                    expected = "git_operation_in_progress"
                else:
                    mode = "publish"
                    expected = "remote_not_found"
                result, payload = repo.cli("inspect", "--mode", mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(payload["error"]["code"], expected)

    def test_sensitive_file_in_unpushed_boundary_commit_is_blocked(self):
        repo = self.make_repo()
        repo.write(".release-readme.yaml", custom_config())
        repo.write("version.json", '{"release":{"version":"0.9.0","build":9}}\n')
        repo.write("README.md", "# Demo\n")
        repo.commit("initial")
        repo.write("version.json", '{"release":{"version":"1.0.0","build":10}}\n')
        repo.write(".env", "PLACEHOLDER_TOKEN_VALUE\n")
        repo.commit("release boundary")
        repo.write("version.json", '{"release":{"version":"1.1.0","build":11}}\n')
        repo.write(
            "README.md",
            "# Demo\n\n## Release Notes\n\n### 1.1.0 - {}\n\n- Ready\n".format(
                dt.date.today().isoformat()
            ),
        )
        repo.commit("current release")
        remote = self.make_bare()
        repo.git("remote", "add", "origin", str(remote))
        result, payload = repo.stage("1.1.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["error"]["code"], "sensitive_content")

    def test_static_check_failure_does_not_commit(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        remote = self.make_bare()
        repo.git("remote", "add", "origin", str(remote))
        config = custom_config().replace("  commands: []", "  commands:\n    - exit 7")
        repo.write(".release-readme.yaml", config)
        repo.write(
            "README.md",
            "# Demo\n\n## Release Notes\n\n### 1.1.0 - {}\n\n- Ready\n".format(
                dt.date.today().isoformat()
            ),
        )
        before = repo.git("rev-parse", "HEAD").stdout.strip()
        result, payload = repo.stage("1.1.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["error"]["code"], "static_check_failed")
        self.assertEqual(repo.git("rev-parse", "HEAD").stdout.strip(), before)

    def test_static_check_same_path_mutation_stops_before_commit(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        remote = self.make_bare()
        repo.git("remote", "add", "origin", str(remote))
        config = custom_config().replace(
            "  commands: []",
            "  commands:\n    - printf check-change >> modified.txt",
        )
        repo.write(".release-readme.yaml", config)
        repo.write(
            "README.md",
            "# Demo\n\n## Release Notes\n\n### 1.1.0 - {}\n\n- Ready\n".format(
                dt.date.today().isoformat()
            ),
        )
        repo.write("modified.txt", "before check\n")
        before = repo.git("rev-parse", "HEAD").stdout.strip()
        result, payload = repo.stage("1.1.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["error"]["code"], "workspace_changed_by_check")
        self.assertEqual(repo.git("rev-parse", "HEAD").stdout.strip(), before)

    def test_invocation_check_and_makefile_detection(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        repo.write(
            ".release-readme.yaml",
            "version:\n"
            "  provider: custom\n"
            "  file: version.json\n"
            "  field: release.version\n"
            "  build_field: release.build\n",
        )
        repo.write(
            "README.md",
            "# Demo\n\n## Release Notes\n\n### 1.1.0 - {}\n\n- Ready\n".format(
                dt.date.today().isoformat()
            ),
        )
        remote = self.make_bare()
        repo.git("remote", "add", "origin", str(remote))
        result, payload = repo.stage("1.1.0", "true", check=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["check_source"], "invocation")
        self.assertEqual(payload["checks"], [{"command": "true", "exit_code": 0}])

        other = self.make_repo()
        initialize_custom(other, committed_current=True)
        other.write(
            ".release-readme.yaml",
            "version:\n"
            "  provider: custom\n"
            "  file: version.json\n"
            "  field: release.version\n"
            "  build_field: release.build\n",
        )
        other.write("Makefile", "lint:\n\t@echo lint\n")
        _, inspect_payload = other.cli("inspect", "--mode", "preview", check=True)
        self.assertEqual(inspect_payload["static_checks"], ["make lint"])

    def test_pre_commit_workspace_change_blocks_push(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        repo.write(
            "README.md",
            "# Demo\n\n## Release Notes\n\n### 1.1.0 - {}\n\n- Ready\n".format(
                dt.date.today().isoformat()
            ),
        )
        repo.write("modified.txt", "before hook\n")
        remote = self.make_bare()
        repo.git("remote", "add", "origin", str(remote))
        stage_result, stage_payload = repo.stage("1.1.0", check=True)
        self.assertEqual(stage_result.returncode, 0)
        hook = repo.root / ".git" / "hooks" / "pre-commit"
        hook.write_text(
            "#!/bin/sh\nprintf hook-change >> modified.txt\n",
            encoding="ascii",
        )
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        before = repo.git("rev-parse", "HEAD").stdout.strip()
        result, payload = repo.publish_plan(stage_payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["error"]["code"], "post_commit_mismatch")
        self.assertNotEqual(repo.git("rev-parse", "HEAD").stdout.strip(), before)
        remote_ref = run(
            ["git", "--git-dir", str(remote), "show-ref", "--verify", "refs/heads/main"],
            repo.root,
            check=False,
        )
        self.assertNotEqual(remote_ref.returncode, 0)

    def test_commit_message_hook_change_blocks_push(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        repo.write(
            "README.md",
            "# Demo\n\n## Release Notes\n\n### 1.1.0 - {}\n\n- Ready\n".format(
                dt.date.today().isoformat()
            ),
        )
        repo.write("modified.txt", "before hook\n")
        remote = self.make_bare()
        repo.git("remote", "add", "origin", str(remote))
        stage_result, stage_payload = repo.stage("1.1.0", check=True)
        self.assertEqual(stage_result.returncode, 0)
        hook = repo.root / ".git" / "hooks" / "commit-msg"
        hook.write_text(
            "#!/bin/sh\nprintf 'TOKEN=not-a-real-token-1234567890\\n' > \"$1\"\n",
            encoding="ascii",
        )
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

        result, payload = repo.publish_plan(stage_payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["error"]["code"], "post_commit_mismatch")
        self.assertTrue(
            payload["error"]["details"]["commit_message_changed"]
        )
        self.assertNotIn("not-a-real-token", json.dumps(payload))
        remote_ref = run(
            ["git", "--git-dir", str(remote), "show-ref", "--verify", "refs/heads/main"],
            repo.root,
            check=False,
        )
        self.assertNotEqual(remote_ref.returncode, 0)

    def test_stage_does_not_commit_and_stale_plan_is_rejected(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        repo.write(
            "README.md",
            "# Demo\n\n## Release Notes\n\n### 1.1.0 - {}\n\n- Ready\n".format(
                dt.date.today().isoformat()
            ),
        )
        repo.write("modified.txt", "planned content\n")
        remote = self.make_bare()
        repo.git("remote", "add", "origin", str(remote))

        before = repo.git("rev-parse", "HEAD").stdout.strip()
        stage_result, stage_payload = repo.stage("1.1.0", check=True)
        self.assertEqual(stage_result.returncode, 0)
        self.assertEqual(repo.git("rev-parse", "HEAD").stdout.strip(), before)
        plan = json.loads(
            (repo.root / ".git" / "publish-version-readme-plan.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("token", plan)

        repo.write("modified.txt", "changed after stage\n")
        result, payload = repo.publish_plan(stage_payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["error"]["code"], "release_plan_stale")
        self.assertEqual(repo.git("rev-parse", "HEAD").stdout.strip(), before)

    def test_stage_accepts_rename_as_complete_workspace_change(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        remote = self.make_bare()
        repo.git("remote", "add", "origin", str(remote))
        repo.write(
            "README.md",
            "# Demo\n\n## Release Notes\n\n### 1.1.0 - {}\n\n- Ready\n".format(
                dt.date.today().isoformat()
            ),
        )
        old_path = repo.write("lib/widgets/old_popup.dart", "popup\n")
        repo.commit("add old popup")
        new_path = repo.root / "lib/widgets/popups/old_popup.dart"
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)

        stage_result, stage_payload = repo.stage("1.1.0", check=True)

        self.assertEqual(stage_result.returncode, 0)
        self.assertEqual(stage_payload["result"], "staged")
        self.assertTrue(
            {
                "lib/widgets/old_popup.dart",
                "lib/widgets/popups/old_popup.dart",
            }.issubset(set(stage_payload["staged_paths"]))
        )

    def test_invalid_commit_message_stops_before_staging(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        remote = self.make_bare()
        repo.git("remote", "add", "origin", str(remote))
        repo.write("README.md", "# Demo\n\n## Release Notes\n\n### 1.1.0\n\n- Ready\n")
        before_head = repo.git("rev-parse", "HEAD").stdout
        before_index = repo.git("write-tree").stdout
        cases = [
            (None, "invalid_commit_message"),
            ("", "invalid_commit_message"),
            ("   ", "invalid_commit_message"),
            ("修复登录问题\n补充提示", "invalid_commit_message"),
            ("修复登录问题\r补充提示", "invalid_commit_message"),
            ("API_TOKEN=placeholder_value_1234567890", "sensitive_content"),
        ]
        for message, error in cases:
            with self.subTest(message=message):
                result, payload = repo.stage("1.1.0", message=message)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(payload["error"]["code"], error)
                self.assertNotIn("placeholder_value_1234567890", json.dumps(payload))
                self.assertEqual(repo.git("rev-parse", "HEAD").stdout, before_head)
                self.assertEqual(repo.git("write-tree").stdout, before_index)
                self.assertFalse((repo.root / ".git" / "publish-version-readme-plan.json").exists())

    def test_publish_stages_everything_and_pushes_only_to_local_bare(self):
        repo = self.make_repo()
        initialize_custom(repo, committed_current=True)
        repo.write(
            ".release-readme.yaml",
            custom_config("git:\n  commit_message: 'release: {version}+{build}'\n"),
        )
        remote = self.make_bare()
        repo.git("remote", "add", "origin", str(remote))
        repo.git("branch", "other")
        repo.git("push", "origin", "other:refs/heads/other")
        remote_other = run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/other"],
            repo.root,
        ).stdout.strip()
        other_tree = repo.git("rev-parse", "other^{tree}").stdout.strip()
        advanced_other = repo.git(
            "commit-tree",
            other_tree,
            "-p",
            remote_other,
            "-m",
            "advance other locally",
        ).stdout.strip()
        repo.git("branch", "-f", "other", advanced_other)
        repo.git("config", "push.default", "matching")
        repo.write(
            "README.md",
            "# Demo\n\n## Release Notes\n\n### 1.1.0 - {}\n\n- Ready\n".format(
                dt.date.today().isoformat()
            ),
        )
        repo.write("modified.txt", "modified\n")
        repo.write("staged.txt", "staged\n")
        repo.git("add", "staged.txt")
        repo.write("untracked.txt", "new\n")
        (repo.root / "deleted.txt").unlink()
        repo.write("ignored.log", "ignored\n")

        before_stage = repo.git("rev-parse", "HEAD").stdout.strip()
        first_message = "更新示例文件并补充发布说明"
        stage_result, stage_payload = repo.stage("1.1.0", message=first_message, check=True)
        self.assertEqual(stage_result.returncode, 0)
        self.assertEqual(stage_payload["result"], "staged")
        self.assertEqual(stage_payload["commit_message"], first_message)
        self.assertIn("README.md", stage_payload["staged_summary"])
        self.assertEqual(repo.git("rev-parse", "HEAD").stdout.strip(), before_stage)
        result, payload = repo.publish_plan(stage_payload, check=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["result"], "committed_and_pushed")
        self.assertEqual(repo.git("log", "-1", "--format=%B").stdout.strip(), first_message)
        self.assertEqual(repo.git("status", "--short").stdout.strip(), "")
        local_head = repo.git("rev-parse", "HEAD").stdout.strip()
        remote_head = run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/main"],
            repo.root,
        ).stdout.strip()
        self.assertEqual(local_head, remote_head)
        committed_paths = set(
            repo.git("show", "--format=", "--name-only", "HEAD").stdout.splitlines()
        )
        self.assertTrue(
            {
                "README.md",
                "modified.txt",
                "staged.txt",
                "untracked.txt",
                "deleted.txt",
            }.issubset(committed_paths)
        )
        self.assertNotIn("ignored.log", committed_paths)

        repo.write("modified.txt", "second release commit\n")
        second_message = "补充示例文件内容"
        stage_result, stage_payload = repo.stage("1.1.0", message=second_message, check=True)
        self.assertEqual(stage_result.returncode, 0)
        self.assertEqual(stage_payload["commit_message"], second_message)
        result, payload = repo.publish_plan(stage_payload, check=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["result"], "committed_and_pushed")
        self.assertEqual(repo.git("log", "-1", "--format=%B").stdout.strip(), second_message)
        remote_other_after = run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/other"],
            repo.root,
        ).stdout.strip()
        self.assertEqual(remote_other_after, remote_other)

        hook = repo.root / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\nexit 99\n", encoding="ascii")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        stage_result, stage_payload = repo.stage("1.1.0", message=None, check=True)
        self.assertEqual(stage_result.returncode, 0)
        self.assertEqual(stage_payload["action"], "no-op")
        self.assertIsNone(stage_payload["commit_message"])
        result, payload = repo.publish_plan(stage_payload, check=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["result"], "no_changes")
        self.assertEqual(payload["push"], "not_needed")

    def test_no_changes_pushes_existing_commit_and_push_failure_preserves_commit(self):
        for reject in (False, True):
            with self.subTest(reject=reject):
                repo = self.make_repo()
                initialize_custom(repo, committed_current=True)
                repo.write(
                    "README.md",
                    "# Demo\n\n## Release Notes\n\n### 1.1.0 - {}\n\n- Ready\n".format(
                        dt.date.today().isoformat()
                    ),
                )
                repo.commit("prepare README")
                remote = self.make_bare()
                if reject:
                    hook = remote / "hooks" / "pre-receive"
                    hook.write_text(
                        "#!/bin/sh\n"
                        "printf 'SENTRY_AUTH_TOKEN=placeholder_value_1234567890\\n' >&2\n"
                        "exit 1\n",
                        encoding="ascii",
                    )
                    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
                repo.git("remote", "add", "origin", str(remote))
                before = repo.git("rev-parse", "HEAD").stdout.strip()
                if reject:
                    repo.write("modified.txt", "publish then reject\n")
                stage_result, stage_payload = repo.stage(
                    "1.1.0", message="补充示例文件内容" if reject else None, check=True
                )
                self.assertEqual(stage_result.returncode, 0)
                result, payload = repo.publish_plan(stage_payload)
                if reject:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(payload["error"]["code"], "push_failed")
                    self.assertNotIn(
                        "placeholder_value_1234567890",
                        json.dumps(payload),
                    )
                    self.assertIn("[REDACTED]", json.dumps(payload))
                    after = repo.git("rev-parse", "HEAD").stdout.strip()
                    self.assertNotEqual(after, before)
                    self.assertEqual(payload["error"]["details"]["commit"], after)
                else:
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(payload["result"], "pushed_existing_commits")
                    self.assertEqual(repo.git("rev-parse", "HEAD").stdout.strip(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
