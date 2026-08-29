from store.db import session
from services.cart import CartService
from store.payments import Gateway
class CheckoutService:
    def pay(self): return Gateway().charge()
    def lookup(self, i): return session()
    def refund(self): return Gateway().refund()
