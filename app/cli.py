"""CLI per l'operatore: `python -m app.cli verify-urls --org <uuid> [--course <uuid>]`.

Raccoglie gli URL pubblici dei file (loghi compresi) con le stesse funzioni dell'API e
fa una `HEAD` su ciascuno, segnalando quelli che non rispondono 200.
Codici di uscita: 0 tutti 200, 1 almeno uno no, 2 configurazione non valida o org/corso
inesistente. Contratto: docs/contracts/modules.md §2.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO
from uuid import UUID

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings, get_settings
from app.db.engine import create_engine
from app.db.queries import fetch_course, fetch_lessons, list_courses, list_organizations
from app.files import lesson_files, logo_url

EXIT_OK = 0
EXIT_FAILED_URLS = 1
EXIT_USAGE = 2

_PAGE_SIZE = 100
_FILE_KINDS = ("dispensa", "slide", "discorso", "video", "video_avatar")


class CliError(Exception):
    """Errore che termina il comando con codice 2 (messaggio per l'operatore)."""


@dataclass(frozen=True, slots=True)
class UrlTarget:
    url: str
    label: str  # es. "corso <id> M1.L2 dispensa" oppure "logo"


@dataclass(frozen=True, slots=True)
class UrlResult:
    target: UrlTarget
    status: int | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.status == 200


async def collect_targets(
    conn: AsyncConnection, base_url: str, org_id: UUID, course_id: UUID | None
) -> list[UrlTarget]:
    """Tutti gli URL dell'org (o del solo corso indicato), nell'ordine di corsi e lezioni."""
    orgs = await list_organizations(conn, [org_id])
    if not orgs:
        raise CliError(f"Organizzazione {org_id} inesistente o eliminata.")
    targets: list[UrlTarget] = []
    logo = logo_url(base_url, orgs[0].logo_path)
    if logo is not None and logo.lower().startswith(("http://", "https://")):
        targets.append(UrlTarget(url=logo, label="logo"))

    if course_id is not None:
        if await fetch_course(conn, org_id, course_id) is None:
            raise CliError(f"Corso {course_id} inesistente nell'organizzazione {org_id}.")
        course_ids = [course_id]
    else:
        course_ids = []
        offset = 0
        while True:
            rows, total = await list_courses(
                conn, org_id, status=None, updated_since=None, limit=_PAGE_SIZE, offset=offset
            )
            course_ids.extend(row.id for row in rows)
            offset += len(rows)
            if not rows or offset >= total:
                break

    for cid in course_ids:
        for lesson in await fetch_lessons(conn, cid):
            files = lesson_files(lesson, base_url)
            for kind in _FILE_KINDS:
                entry = getattr(files, kind)
                if entry is not None:
                    label = f"corso {cid} {lesson.lesson_code} {kind}"
                    targets.append(UrlTarget(url=entry.url, label=label))
    return targets


async def check_urls(
    targets: Sequence[UrlTarget],
    *,
    concurrency: int,
    timeout_s: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[UrlResult]:
    """`HEAD` su ogni URL (redirect seguiti), al massimo `concurrency` alla volta."""
    semaphore = asyncio.Semaphore(concurrency)

    async def one(client: httpx.AsyncClient, target: UrlTarget) -> UrlResult:
        async with semaphore:
            try:
                resp = await client.head(target.url)
            except httpx.HTTPError as exc:
                return UrlResult(target=target, status=None, error=type(exc).__name__)
            return UrlResult(target=target, status=resp.status_code, error=None)

    async with httpx.AsyncClient(
        transport=transport, follow_redirects=True, timeout=timeout_s
    ) as client:
        return list(await asyncio.gather(*(one(client, t) for t in targets)))


async def verify_urls(
    settings: Settings,
    org_id: UUID,
    course_id: UUID | None = None,
    *,
    concurrency: int = 4,
    timeout_s: float = 15.0,
    transport: httpx.AsyncBaseTransport | None = None,
    out: TextIO = sys.stdout,
) -> int:
    """Esegue il controllo e stampa il report; restituisce il codice di uscita."""
    try:
        engine = create_engine(settings)
    except (ArgumentError, ValueError) as exc:
        # Solo il tipo dell'errore: il messaggio potrebbe contenere la DATABASE_URL.
        print(
            f"Errore di configurazione: DATABASE_URL non valida ({type(exc).__name__}).", file=out
        )
        return EXIT_USAGE
    try:
        async with engine.connect() as conn:
            targets = await collect_targets(conn, settings.ovh_public_base_url, org_id, course_id)
    except CliError as exc:
        print(f"Errore: {exc}", file=out)
        return EXIT_USAGE
    except (SQLAlchemyError, OSError) as exc:
        # Solo il tipo dell'errore: il messaggio potrebbe contenere la DATABASE_URL.
        print(f"Errore: database non raggiungibile ({type(exc).__name__}).", file=out)
        return EXIT_USAGE
    finally:
        await engine.dispose()

    # Solo HEAD verso lo storage OVH: gli URL di altri host (per esempio un logo assoluto)
    # non vengono contattati dalla VM e non cambiano il codice di uscita.
    ovh_prefix = settings.ovh_public_base_url.rstrip("/") + "/"
    to_check = [t for t in targets if t.url.startswith(ovh_prefix)]
    skipped = [t for t in targets if not t.url.startswith(ovh_prefix)]

    results = await check_urls(
        to_check, concurrency=concurrency, timeout_s=timeout_s, transport=transport
    )
    failed = [r for r in results if not r.ok]
    for r in failed:
        outcome = str(r.status) if r.status is not None else f"errore {r.error}"
        print(f"{outcome}\t{r.target.label}\t{r.target.url}", file=out)
    for t in skipped:
        print(f"saltato (host esterno)\t{t.label}\t{t.url}", file=out)
    print(
        f"URL controllati: {len(results)}, OK: {len(results) - len(failed)}, "
        f"non OK: {len(failed)}, saltati: {len(skipped)}",
        file=out,
    )
    return EXIT_FAILED_URLS if failed else EXIT_OK


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("deve essere almeno 1")
    return number


def _positive_float(value: str) -> float:
    number = float(value)
    if not number > 0:
        raise argparse.ArgumentTypeError("deve essere maggiore di 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-urls", help="controlla con HEAD gli URL dei file su OVH")
    verify.add_argument("--org", type=UUID, required=True, help="UUID dell'organizzazione")
    verify.add_argument("--course", type=UUID, default=None, help="UUID di un solo corso")
    verify.add_argument("--concurrency", type=_positive_int, default=4)
    verify.add_argument("--timeout", type=_positive_float, default=15.0, help="secondi")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punto d'ingresso; argparse esce già con 2 sugli argomenti non validi."""
    args = build_parser().parse_args(argv)
    try:
        settings = get_settings()  # valori dall'ambiente
    except ValidationError as exc:
        fields = ", ".join(str(err["loc"][0]) for err in exc.errors() if err["loc"])
        print(f"Errore di configurazione: variabili mancanti o non valide ({fields}).")
        return EXIT_USAGE
    return asyncio.run(
        verify_urls(
            settings,
            args.org,
            args.course,
            concurrency=args.concurrency,
            timeout_s=args.timeout,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
