"""Konvensi search/offset pagination yang dipakai semua endpoint list."""

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def normalize(page: int, page_size: int) -> tuple[int, int]:
    page = max(page, 1)
    if page_size < 1:
        page_size = DEFAULT_PAGE_SIZE
    return page, min(page_size, MAX_PAGE_SIZE)


def offset(page: int, page_size: int) -> int:
    return (page - 1) * page_size


def meta(total: int, page: int, page_size: int) -> dict:
    total_page = (total + page_size - 1) // page_size if total > 0 else 0
    return {
        "total_data": total,
        "total_page": total_page,
        "current_page": page,
        "page_size": page_size,
    }
