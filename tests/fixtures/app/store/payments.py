from store.db import session
class Gateway:
    def charge(self): return session()
    def refund(self): return session()
