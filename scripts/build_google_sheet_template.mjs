import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = "google_sheet_template";
const csvDir = `${outputDir}/csv`;
const outputXlsx = `${outputDir}/tsmc_monitor_google_sheet_template.xlsx`;

const sheets = {
  README: [
    ["TSMC Public Monitor - Google Sheet 後台"],
    [""],
    ["用途", "這份試算表是 dashboard 的人工審核後台。GitHub Actions 每天讀取發布成 CSV 的分頁，合併 Yahoo Finance 與公開資料後產生 data/data.json 與 data/data.js。"],
    ["主要分頁", "manual_events：手動新增新聞/事件；watchlist：追蹤清單；sources：常用來源；rules：關鍵字分類規則；companies：未來擴充多公司用。"],
    ["目前程式會讀取", "manual_events 與 watchlist。其他分頁先作為資料名單與後續擴充。"],
    ["發布方式", "Google Sheet 匯入後，將 manual_events 與 watchlist 各自發布為 CSV，並把網址填入 GitHub Secrets。"],
    ["GitHub Secrets", "GOOGLE_SHEET_EVENTS_CSV_URL、GOOGLE_SHEET_WATCHLIST_CSV_URL"],
    ["注意", "不要把私人資訊或非公開資料放入會發布成 CSV 的分頁。"],
  ],
  manual_events: [
    [
      "id",
      "date",
      "title",
      "category",
      "sentiment",
      "importance",
      "dashboard_tag",
      "summary",
      "analyst_take",
      "source_name",
      "source_url",
      "enabled",
      "reviewed_by",
      "updated_at",
    ],
    [
      "",
      "2026-05-26",
      "範例：台積電追蹤事件",
      "重要新聞",
      "neutral",
      "medium",
      "Manual",
      "這是一筆範例資料，正式使用時可刪除。",
      "分析師觀點填在這裡。",
      "Example source",
      "https://example.com",
      "TRUE",
      "",
      "2026-05-26",
    ],
  ],
  watchlist: [
    ["date", "item", "why_it_matters", "expected_source_id", "owner", "status", "updated_at"],
    [
      "2026-06-10",
      "TSMC Monthly Sales - May 2026",
      "驗證 AI/HPC 與先進製程需求是否延續。",
      "SRC_TSMC_FINANCIAL_CALENDAR",
      "",
      "open",
      "2026-05-26",
    ],
    [
      "rolling",
      "CoWoS / HBM / ABF 供應鏈",
      "先進封裝仍是 AI 出貨瓶頸，需追蹤供需缺口。",
      "SRC_REUTERS_TECH_SYMPOSIUM",
      "",
      "open",
      "2026-05-26",
    ],
  ],
  sources: [
    ["source_name", "source_type", "url", "priority", "enabled", "notes"],
    ["TSMC Press Releases", "official", "https://pr.tsmc.com/english", "1", "TRUE", "台積電官方新聞稿"],
    ["TSMC Investor Relations", "official", "https://investor.tsmc.com/english", "1", "TRUE", "財報、月營收、法說會"],
    ["Yahoo Finance 2330.TW", "stock_price", "https://finance.yahoo.com/quote/2330.TW", "2", "TRUE", "股價來源"],
    ["Reuters", "public_news", "https://www.reuters.com", "2", "TRUE", "公開新聞"],
    ["Focus Taiwan", "public_news", "https://focustaiwan.tw", "3", "TRUE", "公開新聞"],
  ],
  rules: [
    ["keyword", "category", "sentiment", "weight", "notes"],
    ["CoWoS", "CoWoS / advanced packaging", "bullish", "4", "若搭配擴產/良率提升通常偏利多"],
    ["2nm", "先進製程", "bullish", "5", "客戶導入、量產爬坡、產能擴充"],
    ["Arizona", "海外設廠", "mixed", "4", "供應安全利多，但成本與執行風險需追蹤"],
    ["export control", "市場風險", "bearish", "4", "出口管制/地緣政治"],
    ["gross margin", "財報", "mixed", "3", "毛利率高低與指引變化"],
  ],
  companies: [
    ["ticker", "company_name", "enabled", "official_ir_url", "press_url", "finance_source", "notes"],
    ["2330.TW", "TSMC", "TRUE", "https://investor.tsmc.com/english", "https://pr.tsmc.com/english", "Yahoo Finance", "目前 dashboard 主追蹤標的"],
    ["TSM", "TSMC ADR", "FALSE", "https://investor.tsmc.com/english", "https://pr.tsmc.com/english", "Yahoo Finance", "未來可擴充 ADR"],
  ],
};

const workbook = Workbook.create();

function writeSheet(name, rows) {
  const sheet = workbook.worksheets.add(name);
  const width = Math.max(...rows.map((row) => row.length));
  const normalized = rows.map((row) => [...row, ...Array(width - row.length).fill("")]);
  sheet.getRangeByIndexes(0, 0, normalized.length, width).values = normalized;
  sheet.freezePanes.freezeRows(name === "README" ? 0 : 1);
  sheet.showGridLines = true;

  const used = sheet.getRangeByIndexes(0, 0, normalized.length, width);
  used.format = {
    font: { name: "Aptos", size: 10 },
    wrapText: true,
    verticalAlignment: "top",
  };

  if (name === "README") {
    sheet.getRange("A1:B1").merge();
    sheet.getRange("A1").format = {
      fill: "#101112",
      font: { bold: true, color: "#F4F0E8", size: 16 },
    };
    sheet.getRange("A3:A8").format = {
      fill: "#202326",
      font: { bold: true, color: "#FFC857" },
    };
    sheet.getRange("A:B").format.columnWidthPx = 220;
    sheet.getRange("B:B").format.columnWidthPx = 720;
    return;
  }

  const header = sheet.getRangeByIndexes(0, 0, 1, width);
  header.format = {
    fill: "#181A1C",
    font: { bold: true, color: "#F4F0E8" },
    horizontalAlignment: "center",
  };

  used.format.borders = {
    bottom: { style: "continuous", color: "#34373A" },
  };

  for (let col = 0; col < width; col += 1) {
    const headerText = normalized[0][col] || "";
    const px = Math.min(360, Math.max(90, headerText.length * 12 + 42));
    sheet.getRangeByIndexes(0, col, Math.max(20, normalized.length), 1).format.columnWidthPx = px;
  }

  const tableRows = Math.max(normalized.length, 20);
  const tableRange = sheet.getRangeByIndexes(0, 0, tableRows, width);
  tableRange.format.wrapText = true;

  if (name === "manual_events") {
    sheet.getRange("E2:E200").dataValidation = { rule: { type: "list", values: ["bullish", "bearish", "mixed", "neutral"] } };
    sheet.getRange("F2:F200").dataValidation = { rule: { type: "list", values: ["high", "medium", "low"] } };
    sheet.getRange("L2:L200").dataValidation = { rule: { type: "list", values: ["TRUE", "FALSE"] } };
  }

  if (name === "watchlist") {
    sheet.getRange("F2:F200").dataValidation = { rule: { type: "list", values: ["open", "watching", "done", "paused"] } };
  }
}

function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

async function writeCsv(name, rows) {
  const text = rows.map((row) => row.map(csvEscape).join(",")).join("\n") + "\n";
  await fs.writeFile(`${csvDir}/${name}.csv`, text, "utf8");
}

await fs.mkdir(csvDir, { recursive: true });

for (const [name, rows] of Object.entries(sheets)) {
  writeSheet(name, rows);
  await writeCsv(name, rows);
}

const readmePreview = await workbook.render({ sheetName: "README", autoCrop: "all", scale: 1, format: "png" });
await fs.writeFile(`${outputDir}/README_preview.png`, new Uint8Array(await readmePreview.arrayBuffer()));

const eventPreview = await workbook.render({ sheetName: "manual_events", autoCrop: "all", scale: 1, format: "png" });
await fs.writeFile(`${outputDir}/manual_events_preview.png`, new Uint8Array(await eventPreview.arrayBuffer()));

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputXlsx);

console.log(`Wrote ${outputXlsx}`);
