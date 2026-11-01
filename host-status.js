(() => {
  const bar = document.querySelector('.host-bar');
  if (!bar) return;
  const state = bar.querySelector('[data-host-state]');
  const cpu = bar.querySelector('[data-host-cpu]');
  const memory = bar.querySelector('[data-host-memory]');
  const disk = bar.querySelector('[data-host-disk]');
  const percent = value => `${Math.round(value)}%`;
  async function refreshHost() {
    try {
      const response = await fetch('/api/host-status', {cache: 'no-store', signal: AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error('Host status unavailable');
      const data = await response.json();
      bar.dataset.state = 'online';
      state.textContent = 'Online';
      cpu.textContent = percent(data.cpu_percent);
      memory.textContent = percent(data.memory_percent);
      disk.textContent = percent(data.disk_percent);
    } catch (_) {
      bar.dataset.state = 'offline';
      state.textContent = 'Unavailable';
      cpu.textContent = memory.textContent = disk.textContent = '—';
    } finally {
      setTimeout(refreshHost, 5000);
    }
  }
  refreshHost();
})();
