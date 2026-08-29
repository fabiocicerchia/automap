export interface Repository { find(id: string): void; }
export class ProductView {
  sku: string;
  label: string;
  price: number;
  render(): string { return this.label; }
}
export class CartView {
  items: ProductView[];
  owner: string;
  add(p: ProductView): void {}
  clear(): void {}
}
