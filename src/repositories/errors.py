class RepositoryError(Exception):
    """Repository 오류의 기반 클래스."""


class NotFoundError(RepositoryError):
    """찾는 행이 없다."""
