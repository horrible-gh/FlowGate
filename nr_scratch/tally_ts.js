const TOKEN = "YRVsFuFuGbR3gJvEA4GWpgt3LYHBBh7gzwl0_iaNdPE";

async function fetchPage(offset) {
  const url = "http://127.0.0.1:8089/flowgate/api/v1/search/documents?q=.&project=flowgate&type=TS&limit=200&offset=" + offset;
  const res = await fetch(url, { headers: { Authorization: "Bearer " + TOKEN } });
  return res.json();
}

(async () => {
  const counts = {};
  const offsets = [0, 200, 400];
  for (const offset of offsets) {
    const j = await fetchPage(offset);
    for (const it of j.items) {
      counts[it.type] = (counts[it.type] || 0) + 1;
    }
  }
  console.log(JSON.stringify(counts));
})();
