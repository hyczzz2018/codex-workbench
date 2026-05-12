from app.services.dev_shelf_common import DevShelfCommonMixin
from app.services.dev_shelf_read import DevShelfReadMixin
from app.services.dev_shelf_write import DevShelfWriteMixin
from app.services.dev_shelf_gateway import DevShelfGatewayMixin
from app.services.dev_shelf_models import DevShelfModelsMixin


class DevShelfReadService(
    DevShelfCommonMixin,
    DevShelfReadMixin,
    DevShelfWriteMixin,
    DevShelfGatewayMixin,
    DevShelfModelsMixin,
):
    """dev-shelf service composed from mixin modules."""


dev_shelf_service = DevShelfReadService()
