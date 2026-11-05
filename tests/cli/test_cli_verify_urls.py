"""Test della CLI verify-urls: DB di test via factories, rete simulata con httpx.MockTransport."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

import app.cli as cli
from app.cli import (
    EXIT_FAILED_URLS,
    EXIT_OK,
    EXIT_USAGE,
    UrlTarget,
    build_parser,
    check_urls,
    main,
    verify_urls,
)
from app.config import Settings, get_settings
from tests.factories import create_course, create_lesson, create_module, create_org, create_user

BASE = "https://media.test/media"
GEN = datetime(2026, 2, 1, tzinfo=UTC)


async def _seed(admin_conn: AsyncConnection) -> tuple[UUID, UUID, UUID]:
    """Org con logo e due corsi: (org, corso_a, corso_b). 2 URL nel corso A, 1 nel corso B."""
    user = await create_user(admin_conn)
    org = await create_org(admin_conn, logo_path="/uploads/organizations/logo.png")
    course_a = await create_course(admin_conn, org_id=org, assignee_id=user)
    course_b = await create_course(admin_conn, org_id=org, assignee_id=user)
    mod_a = await create_module(admin_conn, course_id=course_a, position=1)
    mod_b = await create_module(admin_conn, course_id=course_b, position=1)
    await create_lesson(
        admin_conn,
        module_id=mod_a,
        course_id=course_a,
        position=1,
        pdf_status="ready",
        pdf_path="o/a/l1.pdf",
        pdf_generated_at=GEN,
        video_status="ready",
        video_path="lesson_videos/a/l1.mp4",
    )
    await create_lesson(
        admin_conn,
        module_id=mod_b,
        course_id=course_b,
        position=1,
        slides_pdf_status="ready",
        slides_pdf_path="o/b/l1_slides.pdf",
    )
    return org, course_a, course_b


def _transport(
    seen: list[httpx.Request], fail: Callable[[str], int | Exception | None] = lambda _: None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        outcome = fail(str(request.url))
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome or 200)

    return httpx.MockTransport(handler)


async def test_all_ok_exit_0(
    admin_conn: AsyncConnection, make_settings: Callable[..., Settings]
) -> None:
    org, _, _ = await _seed(admin_conn)
    seen: list[httpx.Request] = []
    out = io.StringIO()

    code = await verify_urls(make_settings(), org, transport=_transport(seen), out=out)

    assert code == EXIT_OK
    assert {r.method for r in seen} == {"HEAD"}
    assert sorted(str(r.url) for r in seen) == sorted(
        [
            f"{BASE}/uploads/organizations/logo.png",
            f"{BASE}/generated_pdfs/o/a/l1.pdf?v={int(GEN.timestamp())}",
            f"{BASE}/uploads/lesson_videos/a/l1.mp4",
            f"{BASE}/generated_pdfs/o/b/l1_slides.pdf",
        ]
    )
    assert "URL controllati: 4, OK: 4, non OK: 0" in out.getvalue()


async def test_404_and_network_error_exit_1(
    admin_conn: AsyncConnection, make_settings: Callable[..., Settings]
) -> None:
    org, _, _ = await _seed(admin_conn)

    def fail(url: str) -> int | Exception | None:
        if url.endswith(".mp4"):
            return 404
        if "slides" in url:
            return httpx.ConnectError("rete giù")
        return None

    out = io.StringIO()
    code = await verify_urls(make_settings(), org, transport=_transport([], fail), out=out)

    assert code == EXIT_FAILED_URLS
    text = out.getvalue()
    lines = [line for line in text.splitlines() if "\t" in line]
    assert len(lines) == 2
    assert any(line.startswith("404\t") and "M1.L1 video" in line for line in lines)
    assert any(line.startswith("errore ConnectError\t") and "slide" in line for line in lines)
    assert "URL controllati: 4, OK: 2, non OK: 2" in text


async def test_redirects_are_followed(
    admin_conn: AsyncConnection, make_settings: Callable[..., Settings]
) -> None:
    org, course_a, _ = await _seed(admin_conn)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "media.test":
            return httpx.Response(302, headers={"Location": "https://cdn.test/x"})
        return httpx.Response(200)

    code = await verify_urls(
        make_settings(), org, course_a, transport=httpx.MockTransport(handler), out=io.StringIO()
    )
    assert code == EXIT_OK


async def test_single_course(
    admin_conn: AsyncConnection, make_settings: Callable[..., Settings]
) -> None:
    org, _, course_b = await _seed(admin_conn)
    seen: list[httpx.Request] = []

    code = await verify_urls(
        make_settings(), org, course_b, transport=_transport(seen), out=io.StringIO()
    )

    assert code == EXIT_OK
    assert sorted(str(r.url) for r in seen) == [
        f"{BASE}/generated_pdfs/o/b/l1_slides.pdf",
        f"{BASE}/uploads/organizations/logo.png",
    ]


async def test_unknown_org_exit_2(
    admin_conn: AsyncConnection, make_settings: Callable[..., Settings]
) -> None:
    await _seed(admin_conn)
    seen: list[httpx.Request] = []
    out = io.StringIO()
    code = await verify_urls(make_settings(), uuid4(), transport=_transport(seen), out=out)
    assert code == EXIT_USAGE
    assert seen == []
    assert "inesistente" in out.getvalue()


async def test_deleted_org_exit_2(
    admin_conn: AsyncConnection, make_settings: Callable[..., Settings]
) -> None:
    org = await create_org(admin_conn, deleted_at=GEN)
    code = await verify_urls(make_settings(), org, transport=_transport([]), out=io.StringIO())
    assert code == EXIT_USAGE


async def test_course_of_other_org_exit_2(
    admin_conn: AsyncConnection, make_settings: Callable[..., Settings]
) -> None:
    org, _, _ = await _seed(admin_conn)
    other_org, other_course, _ = await _seed(admin_conn)
    assert other_org != org
    code = await verify_urls(
        make_settings(), org, other_course, transport=_transport([]), out=io.StringIO()
    )
    assert code == EXIT_USAGE


async def test_org_without_files_exit_0(
    admin_conn: AsyncConnection, make_settings: Callable[..., Settings]
) -> None:
    org = await create_org(admin_conn)
    out = io.StringIO()
    code = await verify_urls(make_settings(), org, transport=_transport([]), out=out)
    assert code == EXIT_OK
    assert "URL controllati: 0" in out.getvalue()


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["verify-urls"],
        ["verify-urls", "--org", "non-uuid"],
        ["verify-urls", "--org", str(uuid4()), "--concurrency", "0"],
        ["verify-urls", "--org", str(uuid4()), "--timeout", "-1"],
    ],
)
def test_invalid_arguments_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(argv)
    assert exc_info.value.code == 2


def test_parser_defaults() -> None:
    org = uuid4()
    args = build_parser().parse_args(["verify-urls", "--org", str(org)])
    assert (args.org, args.course, args.concurrency, args.timeout) == (org, None, 4, 15.0)


def test_missing_configuration_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.config import get_settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OVH_PUBLIC_BASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        code = main(["verify-urls", "--org", str(uuid4())])
    finally:
        get_settings.cache_clear()
    assert code == EXIT_USAGE
    assert "configurazione" in capsys.readouterr().out


async def test_unreachable_db_exit_2_without_leaking_url(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(database_url="postgresql://u:pw-segreta@127.0.0.1:1/db")
    out = io.StringIO()
    code = await verify_urls(settings, uuid4(), transport=_transport([]), out=out)
    assert code == EXIT_USAGE
    assert "pw-segreta" not in out.getvalue()
    assert "non raggiungibile" in out.getvalue()


@pytest.mark.parametrize(
    "bad_url", ["postgresql://u:pw-segreta@127.0.0.1:abc/db", "nonsense://u:pw-segreta@h/db"]
)
async def test_malformed_database_url_exit_2(
    bad_url: str, make_settings: Callable[..., Settings]
) -> None:
    # model_copy non rivalida: simula una URL che supera Settings ma non create_engine.
    settings = make_settings().model_copy(update={"database_url": SecretStr(bad_url)})
    out = io.StringIO()
    code = await verify_urls(settings, uuid4(), transport=_transport([]), out=out)
    assert code == EXIT_USAGE
    assert "pw-segreta" not in out.getvalue()
    assert "configurazione" in out.getvalue()


async def test_external_logo_is_skipped_not_contacted(
    admin_conn: AsyncConnection, make_settings: Callable[..., Settings]
) -> None:
    org = await create_org(admin_conn, logo_path="https://cdn.example/logo.png")
    user = await create_user(admin_conn)
    course = await create_course(admin_conn, org_id=org, assignee_id=user)
    module = await create_module(admin_conn, course_id=course, position=1)
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course,
        position=1,
        pdf_status="ready",
        pdf_path="o/c/l.pdf",
    )
    seen: list[httpx.Request] = []
    out = io.StringIO()

    code = await verify_urls(make_settings(), org, transport=_transport(seen), out=out)

    assert code == EXIT_OK
    assert [str(r.url) for r in seen] == [f"{BASE}/generated_pdfs/o/c/l.pdf"]
    text = out.getvalue()
    assert "saltato (host esterno)\tlogo\thttps://cdn.example/logo.png" in text
    assert "URL controllati: 1, OK: 1, non OK: 0, saltati: 1" in text


async def test_concurrency_limit_is_respected() -> None:
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200)

    targets = [UrlTarget(url=f"{BASE}/uploads/f{i}.mp4", label=f"f{i}") for i in range(8)]
    results = await check_urls(
        targets, concurrency=2, timeout_s=5, transport=httpx.MockTransport(handler)
    )

    assert len(results) == 8
    assert all(r.ok for r in results)
    assert peak == 2


async def test_targets_are_read_in_one_repeatable_read_transaction(
    admin_conn: AsyncConnection,
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org, _, _ = await _seed(admin_conn)
    levels: list[str] = []
    original = cli.collect_targets

    async def spy(conn: AsyncConnection, *args: object, **kwargs: object) -> list[UrlTarget]:
        result = await original(conn, *args, **kwargs)  # type: ignore[arg-type]
        # Stessa transazione delle query di raccolta: il livello deve essere REPEATABLE READ.
        levels.append((await conn.execute(text("SHOW transaction_isolation"))).scalar_one())
        return result

    monkeypatch.setattr(cli, "collect_targets", spy)
    code = await verify_urls(make_settings(), org, transport=_transport([]), out=io.StringIO())

    assert code == EXIT_OK
    assert levels == ["repeatable read"]


async def test_main_logs_json_to_stderr_and_keeps_stdout_clean(
    admin_conn: AsyncConnection,
    ro_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    user = await create_user(admin_conn)
    org = await create_org(admin_conn)
    course = await create_course(admin_conn, org_id=org, assignee_id=user)
    module = await create_module(admin_conn, course_id=course, position=1)
    # Path non sicuro: nessun URL da controllare, ma un warning `unsafe_file_path`.
    await create_lesson(
        admin_conn,
        module_id=module,
        course_id=course,
        position=1,
        pdf_status="ready",
        pdf_path="../fuori.pdf",
    )
    monkeypatch.setenv("DATABASE_URL", ro_database_url)
    monkeypatch.setenv("OVH_PUBLIC_BASE_URL", BASE)
    root = logging.getLogger()
    previous_level = root.level
    get_settings.cache_clear()
    try:
        # main() usa asyncio.run: va eseguito fuori dal loop dei test.
        code = await asyncio.to_thread(main, ["verify-urls", "--org", str(org)])
    finally:
        get_settings.cache_clear()
        for handler in list(root.handlers):
            if getattr(handler, "_a4u_lms_cli_json", False):
                root.removeHandler(handler)
        root.setLevel(previous_level)

    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert captured.out == "URL controllati: 0, OK: 0, non OK: 0, saltati: 0\n"
    records = [json.loads(line) for line in captured.err.splitlines() if line.startswith("{")]
    unsafe = [r for r in records if r["event"] == "unsafe_file_path"]
    assert len(unsafe) == 1
    assert unsafe[0]["kind"] == "dispensa"
    assert "fuori.pdf" not in captured.err
