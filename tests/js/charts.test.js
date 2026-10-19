/**
 * Tests for dependency-free dashboard charts (charts.js)
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

// Import the charts module
import { readFileSync } from "fs";
import { resolve } from "path";

const chartsCode = readFileSync(resolve("app/static/js/charts.js"), "utf-8");

// Create a minimal environment to execute the charts code
function createChartEnvironment() {
  const ctx = {
    canvas: { width: 400, height: 300, getBoundingClientRect: () => ({ width: 400, height: 300 }) },
    clearRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    fill: vi.fn(),
    arc: vi.fn(),
    fillRect: vi.fn(),
    fillText: vi.fn(),
    strokeRect: vi.fn(),
    scale: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    translate: vi.fn(),
    rotate: vi.fn(),
    closePath: vi.fn(),
    strokeStyle: "#000",
    fillStyle: "#000",
    lineWidth: 1,
    font: "12px sans-serif",
    textAlign: "start",
    textBaseline: "alphabetic",
    lineCap: "butt",
    lineJoin: "miter",
  };

  const canvas = {
    width: 400,
    height: 300,
    getContext: vi.fn(() => ctx),
    getBoundingClientRect: () => ({ width: 400, height: 300 }),
  };

  return { ctx, canvas };
}

describe("TimeSeriesChart", () => {
  let env;

  beforeEach(() => {
    env = createChartEnvironment();
  });

  it("should render without throwing when data is provided", () => {
    // Execute the charts code in a context with canvas
    const { TimeSeriesChart } = eval(chartsCode + "; ({ TimeSeriesChart })");
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
    const chart = new TimeSeriesChart(env.canvas, { data });
    expect(chart).toBeDefined();
  });

  it("should handle empty data gracefully", () => {
    const { TimeSeriesChart } = eval(chartsCode + "; ({ TimeSeriesChart })");
    const chart = new TimeSeriesChart(env.canvas, { data: [] });
    expect(chart).toBeDefined();
  });

  it("should handle single data point", () => {
    const { TimeSeriesChart } = eval(chartsCode + "; ({ TimeSeriesChart })");
    const data = [
      {
        id: "series1",
        values: [{ timestamp: Date.now(), value: 100 }],
      },
    ];
    const chart = new TimeSeriesChart(env.canvas, { data });
    expect(chart).toBeDefined();
  });

  it("should update data via setData", () => {
    const { TimeSeriesChart } = eval(chartsCode + "; ({ TimeSeriesChart })");
    const data = [
      {
        id: "series1",
        values: [{ timestamp: Date.now(), value: 100 }],
      },
    ];
    const chart = new TimeSeriesChart(env.canvas, { data });
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
  let env;

  beforeEach(() => {
    env = createChartEnvironment();
  });

  it("should render without throwing when data is provided", () => {
    const { BreakdownChart } = eval(chartsCode + "; ({ BreakdownChart })");
    const data = [
      { source: "chat", value: 1000, label: "Chat" },
      { source: "analysis", value: 500, label: "Analysis" },
      { source: "tools", value: 250, label: "Tools" },
    ];
    const chart = new BreakdownChart(env.canvas, { data });
    expect(chart).toBeDefined();
  });

  it("should handle empty data gracefully", () => {
    const { BreakdownChart } = eval(chartsCode + "; ({ BreakdownChart })");
    const chart = new BreakdownChart(env.canvas, { data: [] });
    expect(chart).toBeDefined();
  });

  it("should update data via setData", () => {
    const { BreakdownChart } = eval(chartsCode + "; ({ BreakdownChart })");
    const data = [{ source: "chat", value: 100, label: "Chat" }];
    const chart = new BreakdownChart(env.canvas, { data });
    const newData = [
      { source: "chat", value: 100, label: "Chat" },
      { source: "analysis", value: 50, label: "Analysis" },
    ];
    chart.setData(newData);
    expect(chart.data).toEqual(newData);
  });

  it("should set total via setTotal", () => {
    const { BreakdownChart } = eval(chartsCode + "; ({ BreakdownChart })");
    const data = [{ source: "chat", value: 100, label: "Chat" }];
    const chart = new BreakdownChart(env.canvas, { data });
    chart.setTotal(1000);
    expect(chart.total).toBe(1000);
  });
});

describe("SparklineChart", () => {
  let env;

  beforeEach(() => {
    env = createChartEnvironment();
  });

  it("should render without throwing when data is provided", () => {
    const { SparklineChart } = eval(chartsCode + "; ({ SparklineChart })");
    const data = [10, 20, 15, 25, 30, 28, 35];
    const chart = new SparklineChart(env.canvas, { data });
    expect(chart).toBeDefined();
  });

  it("should handle empty data gracefully", () => {
    const { SparklineChart } = eval(chartsCode + "; ({ SparklineChart })");
    const chart = new SparklineChart(env.canvas, { data: [] });
    expect(chart).toBeDefined();
  });

  it("should handle single data point", () => {
    const { SparklineChart } = eval(chartsCode + "; ({ SparklineChart })");
    const chart = new SparklineChart(env.canvas, { data: [100] });
    expect(chart).toBeDefined();
  });

  it("should update data via setData", () => {
    const { SparklineChart } = eval(chartsCode + "; ({ SparklineChart })");
    const chart = new SparklineChart(env.canvas, { data: [10, 20] });
    chart.setData([10, 20, 30, 40]);
    expect(chart.data).toEqual([10, 20, 30, 40]);
  });
});

describe("formatNumber", () => {
  it("should format numbers correctly", () => {
    const { formatNumber } = eval(chartsCode + "; ({ formatNumber })");
    expect(formatNumber(0)).toBe("0");
    expect(formatNumber(999)).toBe("999");
    expect(formatNumber(1000)).toBe("1K");
    expect(formatNumber(1500)).toBe("1.5K");
    expect(formatNumber(1000000)).toBe("1M");
    expect(formatNumber(1500000)).toBe("1.5M");
    expect(formatNumber(1000000000)).toBe("1B");
  });
});

describe("formatTime", () => {
  it("should format timestamps", () => {
    const { formatTime } = eval(chartsCode + "; ({ formatTime })");
    const timestamp = new Date("2024-01-15T10:30:00Z").getTime();
    const formatted = formatTime(timestamp);
    expect(formatted).toContain("Jan");
    expect(formatted).toContain("15");
  });
});