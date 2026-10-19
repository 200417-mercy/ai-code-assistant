/**
 * Tests for dependency-free dashboard charts (charts.js)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

const MODULE_PATH = resolve(process.cwd(), "app/static/js/charts.js");

let charts;

beforeAll(() => {
  window.eval(readFileSync(MODULE_PATH, "utf8"));
  charts = window.AICodeAssistant.charts;
  if (!charts || !charts.TimeSeriesChart || !charts.BreakdownChart || !charts.SparklineChart) {
    throw new Error("charts.js did not expose window.AICodeAssistant.charts with expected classes");
  }
});

function createCanvas(width = 400, height = 300) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.getBoundingClientRect = () => ({ width, height });
  return canvas;
}

describe("charts module exports", () => {
  it("exports TimeSeriesChart class", () => {
    expect(typeof charts.TimeSeriesChart).toBe("function");
  });

  it("exports BreakdownChart class", () => {
    expect(typeof charts.BreakdownChart).toBe("function");
  });

  it("exports SparklineChart class", () => {
    expect(typeof charts.SparklineChart).toBe("function");
  });

  it("exports formatNumber function", () => {
    expect(typeof charts.formatNumber).toBe("function");
  });

  it("exports formatTime function", () => {
    expect(typeof charts.formatTime).toBe("function");
  });
});

describe("formatNumber", () => {
  it("formats numbers correctly", () => {
    expect(charts.formatNumber(0)).toBe("0");
    expect(charts.formatNumber(999)).toBe("999");
    expect(charts.formatNumber(1000)).toBe("1K");
    expect(charts.formatNumber(1500)).toBe("1.5K");
    expect(charts.formatNumber(1000000)).toBe("1M");
    expect(charts.formatNumber(1500000)).toBe("1.5M");
    expect(charts.formatNumber(1000000000)).toBe("1B");
  });
});

describe("formatTime", () => {
  it("formats timestamps", () => {
    const timestamp = new Date("2024-01-15T10:30:00Z").getTime();
    const formatted = charts.formatTime(timestamp);
    expect(formatted).toContain("Jan");
    expect(formatted).toContain("15");
  });
});

describe("TimeSeriesChart", () => {
  it("renders without throwing when data is provided", () => {
    const canvas = createCanvas();
    const data = [
      {
        id: "series1",
        label: "Tokens",
        values: [
          { timestamp: Date.now() - 3600000, value: 100 },
          { timestamp: Date.now() - 1800000, value: 150 },
          { timestamp: Date.now(), value: 200 },
        ],
      },
    ];
    const chart = new charts.TimeSeriesChart(canvas, { data });
    expect(chart).toBeDefined();
  });

  it("handles empty data gracefully", () => {
    const canvas = createCanvas();
    const chart = new charts.TimeSeriesChart(canvas, { data: [] });
    expect(chart).toBeDefined();
  });

  it("handles single data point", () => {
    const canvas = createCanvas();
    const data = [
      {
        id: "series1",
        values: [{ timestamp: Date.now(), value: 100 }],
      },
    ];
    const chart = new charts.TimeSeriesChart(canvas, { data });
    expect(chart).toBeDefined();
  });

  it("updates data via setData", () => {
    const canvas = createCanvas();
    const data = [
      {
        id: "series1",
        values: [{ timestamp: Date.now(), value: 100 }],
      },
    ];
    const chart = new charts.TimeSeriesChart(canvas, { data });
    const newData = [
      {
        id: "series1",
        values: [
          { timestamp: Date.now(), value: 100 },
          { timestamp: Date.now() + 1000, value: 200 },
        ],
      },
    ];
    chart.setData(newData);
    expect(chart.data).toEqual(newData);
  });
});

describe("BreakdownChart", () => {
  it("renders without throwing when data is provided", () => {
    const canvas = createCanvas();
    const data = [
      { source: "chat", value: 1000, label: "Chat" },
      { source: "analysis", value: 500, label: "Analysis" },
      { source: "tools", value: 250, label: "Tools" },
    ];
    const chart = new charts.BreakdownChart(canvas, { data });
    expect(chart).toBeDefined();
  });

  it("handles empty data gracefully", () => {
    const canvas = createCanvas();
    const chart = new charts.BreakdownChart(canvas, { data: [] });
    expect(chart).toBeDefined();
  });

  it("updates data via setData", () => {
    const canvas = createCanvas();
    const data = [{ source: "chat", value: 100, label: "Chat" }];
    const chart = new charts.BreakdownChart(canvas, { data });
    const newData = [
      { source: "chat", value: 100, label: "Chat" },
      { source: "analysis", value: 50, label: "Analysis" },
    ];
    chart.setData(newData);
    expect(chart.data).toEqual(newData);
  });

  it("sets total via setTotal", () => {
    const canvas = createCanvas();
    const data = [{ source: "chat", value: 100, label: "Chat" }];
    const chart = new charts.BreakdownChart(canvas, { data });
    chart.setTotal(1000);
    expect(chart.total).toBe(1000);
  });
});

describe("SparklineChart", () => {
  it("renders without throwing when data is provided", () => {
    const canvas = createCanvas(200, 40);
    const data = [10, 20, 15, 25, 30, 28, 35];
    const chart = new charts.SparklineChart(canvas, { data });
    expect(chart).toBeDefined();
  });

  it("handles empty data gracefully", () => {
    const canvas = createCanvas(200, 40);
    const chart = new charts.SparklineChart(canvas, { data: [] });
    expect(chart).toBeDefined();
  });

  it("handles single data point", () => {
    const canvas = createCanvas(200, 40);
    const chart = new charts.SparklineChart(canvas, { data: [100] });
    expect(chart).toBeDefined();
  });

  it("updates data via setData", () => {
    const canvas = createCanvas(200, 40);
    const chart = new charts.SparklineChart(canvas, { data: [10, 20] });
    chart.setData([10, 20, 30, 40]);
    expect(chart.data).toEqual([10, 20, 30, 40]);
  });
});