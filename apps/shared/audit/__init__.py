"""
Moduly Audit 패키지

사용자 작업 감사(Audit) 로그를 비동기로 수집한다.
- record_audit: PostgreSQL Audit Outbox에 저장 (계층 A/B 공용)
"""

from apps.shared.audit.logger import record_audit

__all__ = [
    "record_audit",
]
