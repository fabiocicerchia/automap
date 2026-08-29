from dataclasses import dataclass
from billing.money import Money

@dataclass
class Product:
    sku: str
    name: str
    price: Money
    def discounted(self, pct): return self.price
