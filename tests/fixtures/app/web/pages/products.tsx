import Link from "next/link";
export default function Products() {
  return <div><Link href="/cart">Cart</Link><Link href="/orders/1">Orders</Link></div>;
}
