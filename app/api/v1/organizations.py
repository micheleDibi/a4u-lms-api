"""`GET /v1/organizations`: le organizzazioni associate alla chiave API."""

from fastapi import APIRouter, Request

from app.auth import AuthDep
from app.db.engine import ConnDep
from app.db.queries import OrganizationRow, list_organizations
from app.files import logo_url
from app.logging import get_logger
from app.schemas import OrganizationOut

router = APIRouter()
logger = get_logger(__name__)


def _organization_out(row: OrganizationRow, base_url: str) -> OrganizationOut:
    url = logo_url(base_url, row.logo_path)
    if url is None and row.logo_path is not None and row.logo_path.strip():
        # logo_url ha già loggato `unsafe_logo_path` senza contesto: qui l'org interessata.
        logger.warning("logo_omitted", extra={"org_id": str(row.id)})
    return OrganizationOut(
        id=row.id,
        name=row.name,
        website=row.website,
        logo_url=url,
        courses_count=row.courses_count,
    )


@router.get("/organizations", response_model=list[OrganizationOut])
async def get_organizations(request: Request, ctx: AuthDep, conn: ConnDep) -> list[OrganizationOut]:
    """Organizzazioni della chiave non eliminate, ordinate per nome e id."""
    base_url: str = request.app.state.settings.ovh_public_base_url
    rows = await list_organizations(conn, ctx.org_ids)
    return [_organization_out(row, base_url) for row in rows]
