from dataclasses import dataclass
from enum import Enum

class Currency(Enum):
    USD = "usd"
    EUR = "eur"

@dataclass
class Money:
    amount: int
    currency: Currency
    def add(self, other): return Money(self.amount + other.amount, self.currency)
    def negate(self): return Money(-self.amount, self.currency)
