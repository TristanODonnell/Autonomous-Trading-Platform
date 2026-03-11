from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class OrderExecutionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def persist_order_bundle(
        self,
        intent: OrderIntents,
        broker_order: BrokerOrder,
        fills: list[Fill],
    ) -> None:
        with SorUnitOfWork(self.session) as uow:
            uow.order_intents.upsert(intent)
            uow.broker_orders.upsert(broker_order)
            for fill in fills:
                uow.fills.upsert(fill)
