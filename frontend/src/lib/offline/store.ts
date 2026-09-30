/** Tiny key-value abstraction: IndexedDB in the browser, in-memory in tests/SSR. */
export interface KVStore { get<T>(store: string, key: string): Promise<T | undefined>; put(store: string, key: string, value: unknown): Promise<void>; del(store: string, key: string): Promise<void>; all<T>(store: string): Promise<T[]>; clear(store?: string): Promise<void> }

export class MemoryStore implements KVStore {
  private m = new Map<string, Map<string, unknown>>();
  private s(n: string) { let x = this.m.get(n); if (!x) { x = new Map(); this.m.set(n, x); } return x; }
  async get<T>(store: string, key: string) { return this.s(store).get(key) as T | undefined; }
  async put(store: string, key: string, value: unknown) { this.s(store).set(key, value); }
  async del(store: string, key: string) { this.s(store).delete(key); }
  async all<T>(store: string) { return [...this.s(store).values()] as T[]; }
  async clear(store?: string) { if (store) this.s(store).clear(); else this.m.clear(); }
}

const STORES = ["snap", "queue"];
class IdbStore implements KVStore {
  private db: Promise<IDBDatabase> | null = null;
  private open() {
    this.db ??= new Promise((res, rej) => {
      const r = indexedDB.open("bg-offline", 1);
      r.onupgradeneeded = () => { for (const s of STORES) if (!r.result.objectStoreNames.contains(s)) r.result.createObjectStore(s); };
      r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
    });
    return this.db;
  }
  private async tx<T>(store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    const db = await this.open();
    return new Promise((res, rej) => { const r = fn(db.transaction(store, mode).objectStore(store)); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  }
  get<T>(store: string, key: string) { return this.tx<T | undefined>(store, "readonly", (s) => s.get(key)); }
  async put(store: string, key: string, value: unknown) { await this.tx(store, "readwrite", (s) => s.put(value, key)); }
  async del(store: string, key: string) { await this.tx(store, "readwrite", (s) => s.delete(key)); }
  all<T>(store: string) { return this.tx<T[]>(store, "readonly", (s) => s.getAll()); }
  async clear(store?: string) { for (const n of store ? [store] : STORES) await this.tx(n, "readwrite", (s) => s.clear()); }
}
let impl: KVStore | null = null;
export function kv(): KVStore { return (impl ??= typeof indexedDB !== "undefined" ? new IdbStore() : new MemoryStore()); }
export function setStoreForTests(s: KVStore) { impl = s; }
