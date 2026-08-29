import Link from "next/link";
export default function Cart() {
  return <div><Link href="/checkout">Checkout</Link><Link href="/products">Back</Link></div>;
}
