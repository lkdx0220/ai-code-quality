import json

from ai_code_quality.redact import redact_obj, redact_text

# 测试用的假凭据一律程序化拼接：源码里不出现厂商形态的字面量，
# 避免 GitHub 推送保护按模式误拦（内容当然也都是假的）。
FAKE_OPENAI = "sk-proj-" + "abcdef1234567890" * 2
FAKE_GITHUB = "ghp_" + "0123456789abcdefghijklmnopqrstuv"
FAKE_AWS = "AKIA" + "IOSFODNN7EXAMPLE"
FAKE_ANTHROPIC = "sk-ant-" + "1234567890abcdef" * 2
FAKE_SLACK = "xoxb-" + "1234567890abcdef123"


def test_redact_openai_style_key():
    out = redact_text(f'API_KEY = "{FAKE_OPENAI}"')
    assert FAKE_OPENAI not in out
    assert "***REDACTED***" in out
    assert "API_KEY" in out  # 键名保留


def test_redact_github_token():
    out = redact_text(f"token = {FAKE_GITHUB}")
    assert FAKE_GITHUB not in out


def test_redact_aws_key():
    out = redact_text(FAKE_AWS)
    assert FAKE_AWS not in out


def test_redact_assignment_forms():
    assert "hunter2" not in redact_text("password: 'hunter2super'")
    assert "hunter2" not in redact_text('db_password = "hunter2super"')


def test_redact_db_url():
    out = redact_text("postgres://admin:s3cret@db.local/x")
    assert "s3cret" not in out
    assert "admin" in out and "db.local" in out


def test_redact_private_key_block():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----"
    out = redact_text(text)
    assert "MIIEow" not in out


def test_redact_obj_recursive():
    obj = {"a": [FAKE_ANTHROPIC], "nested": {"token": FAKE_SLACK}}
    out = json.dumps(redact_obj(obj))
    assert FAKE_ANTHROPIC not in out and FAKE_SLACK not in out


def test_normal_code_untouched():
    s = "def add(a: int, b: int) -> int:\n    return a + b\n"
    assert redact_text(s) == s
