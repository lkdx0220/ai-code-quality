import json

from ai_code_quality.redact import redact_obj, redact_text


def test_redact_openai_style_key():
    s = 'API_KEY = "sk-proj-abcdef1234567890abcdef1234567890"'
    out = redact_text(s)
    assert "sk-proj-abcdef" not in out
    assert "***REDACTED***" in out
    assert "API_KEY" in out  # 键名保留


def test_redact_github_token():
    out = redact_text("token = ghp_0123456789abcdefghijklmnopqrstuv")
    assert "ghp_" not in out


def test_redact_aws_key():
    out = redact_text("AKIAIOSFODNN7EXAMPLE")
    assert "AKIA" not in out


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
    obj = {
        "a": ["sk-ant-1234567890abcdef1234567890abc"],
        "nested": {"token": "xoxb-1234567890abcdef123"},
    }
    out = json.dumps(redact_obj(obj))
    assert "sk-ant-" not in out and "xoxb-" not in out


def test_normal_code_untouched():
    s = "def add(a: int, b: int) -> int:\n    return a + b\n"
    assert redact_text(s) == s
