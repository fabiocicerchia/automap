from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from billing.money import Money, Currency

@dataclass
class LineItem:
    sku: str
    quantity: int
    unit_price: Money

class Payable(ABC):
    @abstractmethod
    def total(self): ...

@dataclass
class Invoice(Payable):
    number: str
    lines: list
    currency: Currency
    discount: Money = None
    def total(self): return self.discount
    def add_line(self, item): self.lines.append(item)
    def void(self): self.lines = []

class CreditNote(Invoice):
    def total(self): return self.discount.negate()

class PaymentGateway(ABC):
    @abstractmethod
    def charge(self, invoice, amount): ...

class StripeGateway(PaymentGateway):
    def __init__(self, api_key: str, timeout: int = 30):
        self.api_key = api_key
        self.timeout = timeout
    def charge(self, invoice, amount): return True
    def refund(self, invoice): return True
