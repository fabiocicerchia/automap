import { useRouter } from "next/router";
export default function Checkout() {
  const router = useRouter();
  const done = () => router.push("/orders/1");
  return <button onClick={done}>Pay</button>;
}
