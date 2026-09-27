const pendingWrites = new Map<string, Promise<void>>();

// Each editor remembers the cache it read; Web Locks serialize writes across tabs.
export function createDraftCache(key: string) {
  let observed: string | null = null;
  let pending: Promise<void> = Promise.resolve();
  return {
    async read(): Promise<string | null> {
      await pendingWrites.get(key)?.catch(() => undefined);
      try { observed = localStorage.getItem(key); }
      catch { observed = null; }
      if (observed !== null) return observed;
      try { return sessionStorage.getItem(key); }
      catch { return null; }
    },
    write(value: string | null): Promise<void> {
      const write = async () => {
        if (!navigator.locks) throw new Error("此浏览器不支持安全的跨窗口草稿缓存，请手动保存更改。");
        await navigator.locks.request(key, () => {
          if (localStorage.getItem(key) !== observed) {
            // A clean editor has no authority to remove another editor's draft.
            if (value === null) return;
            throw new Error("另一窗口已更新草稿缓存，当前编辑尚未缓存；请保存更改或导出草稿后重新读取。");
          }
          if (value === null) localStorage.removeItem(key);
          else localStorage.setItem(key, value);
          observed = value;
          sessionStorage.removeItem(key);
        });
      };
      pending = pending.catch(() => undefined).then(write);
      pendingWrites.set(key, pending);
      const queued = pending;
      void queued.finally(() => { if (pendingWrites.get(key) === queued) pendingWrites.delete(key); }).catch(() => undefined);
      return pending;
    },
  };
}
