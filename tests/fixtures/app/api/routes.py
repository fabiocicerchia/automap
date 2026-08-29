from fastapi import FastAPI
from services.cart import CartService
from services.checkout import CheckoutService
app = FastAPI()

@app.get("/products")
def list_products(): return CartService().browse()

@app.get("/cart")
def view_cart(): return CartService().read()

@app.post("/cart/items")
def add_item(): return CartService().add()

@app.post("/checkout")
def checkout(): return CheckoutService().pay()

@app.get("/orders/{id}")
def order_detail(id): return CheckoutService().lookup(id)

@app.get("/admin/refunds")
def refunds(): return CheckoutService().refund()
