"""Tests for parsers.py: one class per manifest format, plus the
shared is_outdated / bump_severity version-comparison logic.
"""

from parsers import (
    parse_package_json, bump_package_json,
    parse_requirements_txt, bump_requirements_txt,
    parse_go_mod, bump_go_mod,
    parse_pyproject_toml, bump_pyproject_toml,
    parse_cargo_toml, bump_cargo_toml,
    parse_gemfile, bump_gemfile,
    parse_composer_json, bump_composer_json,
    is_outdated, bump_severity,
)


class TestPackageJson:
    content = '{"dependencies": {"lodash": "^4.17.0"}, "devDependencies": {"jest": "~29.0.0"}}'

    def test_parse(self):
        assert parse_package_json(self.content) == {"lodash": "^4.17.0", "jest": "~29.0.0"}

    def test_bump_preserves_prefix(self):
        bumped = bump_package_json(self.content, "lodash", "4.18.0")
        assert '"lodash": "^4.18.0"' in bumped


class TestRequirementsTxt:
    content = "requests==2.31.0\nflask>=3.0.0\ndjango~=4.2.0\nclick<=8.0.0\nsomepkg!=1.2.3\nunpinned\n"

    def test_parse_only_supported_operators(self):
        deps = parse_requirements_txt(self.content)
        assert deps == {"requests": "==2.31.0", "flask": ">=3.0.0", "django": "~=4.2.0"}

    def test_bump_preserves_operator(self):
        assert "django~=4.2.15" in bump_requirements_txt(self.content, "django", "4.2.15")
        assert "flask>=3.1.0" in bump_requirements_txt(self.content, "flask", "3.1.0")


class TestGoMod:
    content = "module x\n\nrequire (\n\tgithub.com/gorilla/mux v1.8.0\n)\n"

    def test_parse(self):
        assert parse_go_mod(self.content) == {"github.com/gorilla/mux": "v1.8.0"}

    def test_bump(self):
        assert "v1.9.0" in bump_go_mod(self.content, "github.com/gorilla/mux", "v1.9.0")


class TestPyprojectToml:
    poetry_content = (
        '[tool.poetry.dependencies]\n'
        'python = "^3.10"\n'
        'requests = "^2.31.0"\n'
        'flask = { version = "^3.0", extras = ["async"] }\n'
    )
    pep621_content = (
        '[project]\n'
        'dependencies = [\n'
        '    "requests>=2.31.0",\n'
        '    "click",\n'
        ']\n'
    )

    def test_parse_poetry_style(self):
        deps = parse_pyproject_toml(self.poetry_content)
        assert deps == {"requests": "^2.31.0", "flask": "^3.0"}
        assert "python" not in deps

    def test_bump_poetry_inline_table(self):
        bumped = bump_pyproject_toml(self.poetry_content, "flask", "3.1.0")
        assert 'flask = { version = "^3.1.0", extras = ["async"] }' in bumped

    def test_parse_pep621_array(self):
        deps = parse_pyproject_toml(self.pep621_content)
        assert deps == {"requests": ">=2.31.0"}
        assert "click" not in deps  # unpinned — nothing to compare

    def test_bump_pep621_array(self):
        bumped = bump_pyproject_toml(self.pep621_content, "requests", "2.34.2")
        assert '"requests>=2.34.2"' in bumped


class TestCargoToml:
    content = '[dependencies]\nserde = "1.0.152"\ntokio = { version = "1.28", features = ["full"] }\n'
    nested_content = (
        '[dependencies.regex]\n'
        'version = "1.5.0"\n'
        'features = ["unicode"]\n'
    )

    def test_parse(self):
        assert parse_cargo_toml(self.content) == {"serde": "^1.0.152", "tokio": "^1.28"}

    def test_bump(self):
        assert 'serde = "1.0.229"' in bump_cargo_toml(self.content, "serde", "1.0.229")

    def test_parse_nested_table_style(self):
        deps = parse_cargo_toml(self.nested_content)
        assert deps == {"regex": "^1.5.0"}

    def test_bump_nested_table_preserves_other_keys(self):
        bumped = bump_cargo_toml(self.nested_content, "regex", "1.6.0")
        assert 'version = "1.6.0"' in bumped
        assert 'features = ["unicode"]' in bumped

    def test_bare_version_gets_implicit_caret_for_comparison(self):
        # Cargo treats an unprefixed version as caret by default —
        # confirms is_outdated treats it as a range, not an exact pin.
        deps = parse_cargo_toml(self.content)
        assert is_outdated(deps["serde"], "1.9.9") is False    # still ^1.x, not outdated
        assert is_outdated(deps["serde"], "2.0.0") is True     # escapes major

    def test_explicit_operator_is_left_alone(self):
        content = '[dependencies]\nserde = "~1.0.152"\n'
        assert parse_cargo_toml(content) == {"serde": "~1.0.152"}


class TestGemfile:
    content = 'gem "rails", "7.1.2"\ngem "pg", "~> 1.5"\ngem "puma", ">= 5.0"\ngem "old", "<= 2.0.0"\ngem "x"\n'

    def test_parse(self):
        deps = parse_gemfile(self.content)
        assert deps == {"rails": "7.1.2", "pg": "~=1.5", "puma": ">=5.0"}
        assert "old" not in deps    # explicit ceiling, left alone on purpose
        assert "x" not in deps      # unpinned, no constraint to compare

    def test_bump(self):
        assert 'gem "pg", "~> 1.6.0"' in bump_gemfile(self.content, "pg", "1.6.0")

    def test_pessimistic_operator_is_range_aware(self):
        deps = parse_gemfile(self.content)
        assert is_outdated(deps["pg"], "1.9.0") is False   # ~> 1.5 allows up to <2.0
        assert is_outdated(deps["pg"], "2.0.0") is True    # escapes the range


class TestComposerJson:
    content = (
        '{"require": {"php": "^8.1", "ext-json": "*", "guzzlehttp/guzzle": "^7.0"}, '
        '"require-dev": {"phpunit/phpunit": "^10.0"}}'
    )

    def test_parse_skips_platform_packages(self):
        deps = parse_composer_json(self.content)
        assert deps == {"guzzlehttp/guzzle": "^7.0", "phpunit/phpunit": "^10.0"}

    def test_bump(self):
        import json
        bumped = json.loads(bump_composer_json(self.content, "guzzlehttp/guzzle", "7.9.2"))
        assert bumped["require"]["guzzlehttp/guzzle"] == "^7.9.2"


class TestIsOutdated:
    def test_caret_range_aware(self):
        assert is_outdated("^2.1.0", "2.9.0") is False   # still within ^2.x
        assert is_outdated("^2.1.0", "3.0.0") is True    # escapes the range

    def test_tilde_range_aware(self):
        assert is_outdated("~2.1.0", "2.1.9") is False
        assert is_outdated("~2.1.0", "2.2.0") is True

    def test_pep440_compatible_release(self):
        assert is_outdated("~=2.31", "2.32.0") is False   # 2-segment: locks major only
        assert is_outdated("~=2.31", "3.0.0") is True
        assert is_outdated("~=2.31.4", "2.31.9") is False  # 3-segment: locks major.minor
        assert is_outdated("~=2.31.4", "2.32.0") is True

    def test_plain_pins(self):
        assert is_outdated(">=2.0.0", "2.5.0") is True
        assert is_outdated("==2.5.0", "2.5.0") is False
        assert is_outdated("v1.2.3", "v1.3.0") is True


class TestBumpSeverity:
    def test_levels(self):
        assert bump_severity("2.1.0", "2.1.5") == "patch"
        assert bump_severity("2.1.0", "2.2.0") == "minor"
        assert bump_severity("2.1.0", "3.0.0") == "major"

    def test_unparseable_is_unknown(self):
        assert bump_severity("not-a-version", "1.0.0") == "unknown"
