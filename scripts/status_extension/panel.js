document.getElementById('goFlow').addEventListener('click', async () => {
  const tabs = await chrome.tabs.query({ url: 'https://flow.google.com/*' });
  if (tabs.length > 0) {
    await chrome.tabs.update(tabs[0].id, { active: true });
    await chrome.windows.update(tabs[0].windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url: 'https://flow.google.com/project/6f8960c1-1b98-4e73-b5c0-0e63010bf835' });
  }
});

// 대시보드 서버(8799)가 늦게 켜졌거나 상태가 갱신 안 될 때 iframe을 통째로 다시 불러온다.
document.getElementById('refresh').addEventListener('click', () => {
  const frame = document.getElementById('dash');
  frame.src = frame.src;
});
