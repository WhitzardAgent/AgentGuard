"""Email delivery helpers for user-facing verification messages."""
from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
import os
import smtplib


class EmailDeliveryUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    sender: str
    username: str | None = None
    password: str | None = None
    use_tls: bool = True


class EmailSender:
    def send_verification_code(self, *, to_email: str, code: str) -> None:
        raise NotImplementedError


class SmtpEmailSender(EmailSender):
    def __init__(self, config: SmtpConfig | None = None) -> None:
        self.config = config or _smtp_config_from_env()

    def send_verification_code(self, *, to_email: str, code: str) -> None:
        config = self.config
        message = EmailMessage()
        message["From"] = config.sender
        message["To"] = to_email
        message["Subject"] = "Your AgentGuard verification code"
        message.set_content(
            "\n".join(
                [
                    "Use this code to finish creating your AgentGuard account:",
                    "",
                    code,
                    "",
                    "This code expires shortly. If you did not request it, ignore this email.",
                ]
            )
        )
        try:
            with smtplib.SMTP(config.host, config.port, timeout=10) as smtp:
                if config.use_tls:
                    smtp.starttls()
                if config.username:
                    smtp.login(config.username, config.password or "")
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryUnavailable("SMTP email delivery failed") from exc


def get_email_sender() -> EmailSender:
    return SmtpEmailSender()


def _smtp_config_from_env() -> SmtpConfig:
    host = os.environ.get("AGENTGUARD_SMTP_HOST", "").strip()
    sender = os.environ.get("AGENTGUARD_SMTP_FROM", "").strip()
    if not host or not sender:
        raise EmailDeliveryUnavailable("SMTP email delivery is not configured")
    return SmtpConfig(
        host=host,
        port=_int_env("AGENTGUARD_SMTP_PORT", 587),
        sender=sender,
        username=_optional_text(os.environ.get("AGENTGUARD_SMTP_USERNAME")),
        password=_optional_text(os.environ.get("AGENTGUARD_SMTP_PASSWORD")),
        use_tls=_bool_env("AGENTGUARD_SMTP_USE_TLS", True),
    )


def _optional_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return max(1, value)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
