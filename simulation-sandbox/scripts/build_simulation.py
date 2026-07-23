#!/usr/bin/env python3
"""Generate small runnable simulation sandboxes from JSON rule systems."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


COLORS = {
    "susceptible": "#2775c7",
    "infected": "#d64545",
    "recovered": "#1f9d78",
    "evacuating": "#2775c7",
    "near_exit": "#1f9d78",
    "plant": "#1f9d78",
    "herbivore": "#2775c7",
    "predator": "#d64545",
    "car": "#2775c7",
}


@dataclass
class Result:
    history: list[dict[str, Any]]
    frames: list[dict[str, Any]]
    events: list[str]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def distance2(a: dict[str, float], b: dict[str, float]) -> float:
    dx = a["x"] - b["x"]
    dy = a["y"] - b["y"]
    return dx * dx + dy * dy


def slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text or "simulation"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_presets() -> dict[str, Any]:
    skill_dir = Path(__file__).resolve().parents[1]
    return load_json(skill_dir / "assets" / "presets.json")


def normalize_rules(rules: dict[str, Any], steps: int | None) -> dict[str, Any]:
    normalized = json.loads(json.dumps(rules))
    normalized.setdefault("name", "Simulation Sandbox")
    normalized.setdefault("model", "virus")
    normalized.setdefault("description", "")
    normalized.setdefault("world", {})
    normalized["world"].setdefault("width", 960)
    normalized["world"].setdefault("height", 640)
    normalized["world"].setdefault("agents", 180)
    normalized["world"].setdefault("steps", 600)
    if steps is not None:
        normalized["world"]["steps"] = steps
    normalized.setdefault("rules", {})
    return normalized


def frame_stride(total_steps: int, max_frames: int) -> int:
    return max(1, math.ceil(max(1, total_steps) / max(1, max_frames)))


def capture_particles(step: int, mode: str, agents: list[dict[str, Any]], metrics: dict[str, Any], stride_state: dict[str, Any]) -> None:
    width = stride_state["width"]
    height = stride_state["height"]
    items = []
    for agent in agents:
        state = agent.get("state", "agent")
        items.append({
            "shape": agent.get("shape", "circle"),
            "x": round(agent["x"], 2),
            "y": round(agent["y"], 2),
            "r": agent.get("r", 3.4),
            "color": agent.get("color") or COLORS.get(state, "#647080"),
            "state": state,
        })
    stride_state["frames"].append({"step": step, "mode": mode, "width": width, "height": height, "items": items, "metrics": metrics})


def simulate_virus(rules: dict[str, Any], seed: int, max_frames: int) -> Result:
    rng = random.Random(seed)
    world = rules["world"]
    width, height, steps = world["width"], world["height"], world["steps"]
    counts = {entry.get("type", "susceptible"): int(entry.get("count", 0)) for entry in rules.get("agents", [])}
    if not counts:
        counts = {"susceptible": int(world.get("agents", 180)) - 6, "infected": 6, "recovered": 0}

    agents: list[dict[str, Any]] = []
    for state, count in counts.items():
        for _ in range(max(0, count)):
            angle = rng.random() * math.tau
            agents.append({"x": rng.uniform(24, width - 24), "y": rng.uniform(24, height - 24), "vx": math.cos(angle), "vy": math.sin(angle), "state": state, "age": 0})

    movement = rules["rules"].get("movement", {})
    infection = rules["rules"].get("infection", {})
    recovery = rules["rules"].get("recovery", {})
    speed = float(movement.get("speed", 1.2))
    noise = float(movement.get("noise", 0.35))
    infection_distance2 = float(infection.get("distance", 8)) ** 2
    infection_probability = float(infection.get("probability", 0.02))
    recovery_after = int(recovery.get("afterSteps", 360))
    recovery_probability = float(recovery.get("probability", 0.014))
    stride = frame_stride(steps, max_frames)
    state = {"frames": [], "width": width, "height": height}
    history: list[dict[str, Any]] = []
    events: list[str] = []

    for step in range(steps + 1):
        metrics = {"step": step, "susceptible": 0, "infected": 0, "recovered": 0}
        for agent in agents:
            metrics[agent["state"]] = metrics.get(agent["state"], 0) + 1
        history.append(metrics)
        if step % stride == 0 or step == steps:
            capture_particles(step, "virus", agents, metrics, state)
        if step == steps:
            break

        for agent in agents:
            agent["vx"] += rng.uniform(-noise, noise)
            agent["vy"] += rng.uniform(-noise, noise)
            mag = max(0.001, math.hypot(agent["vx"], agent["vy"]))
            agent["vx"] = agent["vx"] / mag * speed
            agent["vy"] = agent["vy"] / mag * speed
            agent["x"] = clamp(agent["x"] + agent["vx"], 8, width - 8)
            agent["y"] = clamp(agent["y"] + agent["vy"], 8, height - 8)
            if agent["x"] <= 8 or agent["x"] >= width - 8:
                agent["vx"] *= -1
            if agent["y"] <= 8 or agent["y"] >= height - 8:
                agent["vy"] *= -1
            if agent["state"] == "infected":
                agent["age"] += 1

        infected = [agent for agent in agents if agent["state"] == "infected"]
        susceptible = [agent for agent in agents if agent["state"] == "susceptible"]
        new_infections = 0
        for carrier in infected:
            for target in susceptible:
                if target["state"] == "susceptible" and distance2(carrier, target) <= infection_distance2 and rng.random() < infection_probability:
                    target["state"] = "infected"
                    target["age"] = 0
                    new_infections += 1
        recoveries = 0
        for agent in infected:
            if agent["age"] > recovery_after and rng.random() < recovery_probability:
                agent["state"] = "recovered"
                recoveries += 1
        if new_infections and step % 25 == 0:
            events.append(f"step {step}: {new_infections} infections")
        if recoveries and step % 25 == 0:
            events.append(f"step {step}: {recoveries} recoveries")
    return Result(history, state["frames"], events[-12:])


def simulate_evacuation(rules: dict[str, Any], seed: int, max_frames: int) -> Result:
    rng = random.Random(seed)
    world = rules["world"]
    width, height, steps, count = world["width"], world["height"], world["steps"], world["agents"]
    movement = rules["rules"].get("movement", {})
    evacuation = rules["rules"].get("evacuation", {})
    speed = float(movement.get("speed", 1.6))
    noise = float(movement.get("noise", 0.2))
    separation = float(movement.get("separation", 14))
    exits = evacuation.get("exits") or [{"x": width - 18, "y": height / 2 - 55, "width": 18, "height": 110}]
    obstacles = evacuation.get("obstacles") or []
    people = [{"x": rng.uniform(35, width * 0.62), "y": rng.uniform(35, height - 35), "vx": 0.0, "vy": 0.0, "state": "evacuating"} for _ in range(count)]
    stride = frame_stride(steps, max_frames)
    state = {"frames": [], "width": width, "height": height}
    history: list[dict[str, Any]] = []
    events: list[str] = []

    for step in range(steps + 1):
        inside = len([p for p in people if p["state"] != "evacuated"])
        metrics = {"step": step, "inside": inside, "evacuated": count - inside}
        history.append(metrics)
        if step % stride == 0 or step == steps:
            visible = [{**p, "color": COLORS["evacuating" if p.get("exit_distance", width) > width * 0.2 else "near_exit"]} for p in people if p["state"] != "evacuated"]
            frame = {"step": step, "mode": "evacuation", "width": width, "height": height, "items": [], "metrics": metrics, "exits": exits, "obstacles": obstacles}
            for p in visible:
                frame["items"].append({"shape": "circle", "x": round(p["x"], 2), "y": round(p["y"], 2), "r": 3.4, "color": p["color"], "state": "person"})
            state["frames"].append(frame)
        if step == steps or inside == 0:
            break

        evacuated_now = 0
        for person in people:
            if person["state"] == "evacuated":
                continue
            best = min(exits, key=lambda ex: (person["x"] - (ex["x"] + ex["width"] / 2)) ** 2 + (person["y"] - (ex["y"] + ex["height"] / 2)) ** 2)
            target = {"x": best["x"] + best["width"] / 2, "y": best["y"] + best["height"] / 2}
            dx, dy = target["x"] - person["x"], target["y"] - person["y"]
            mag = max(0.001, math.hypot(dx, dy))
            ax, ay = dx / mag, dy / mag
            person["exit_distance"] = mag
            for other in people:
                if other is person or other["state"] == "evacuated":
                    continue
                d2 = distance2(person, other)
                if 0.1 < d2 < separation * separation:
                    d = math.sqrt(d2)
                    ax += (person["x"] - other["x"]) / d * 0.5
                    ay += (person["y"] - other["y"]) / d * 0.5
            for obstacle in obstacles:
                if obstacle["x"] - 18 < person["x"] < obstacle["x"] + obstacle["width"] + 18 and obstacle["y"] - 18 < person["y"] < obstacle["y"] + obstacle["height"] + 18:
                    ax += (person["x"] - (obstacle["x"] + obstacle["width"] / 2)) * 0.012
                    ay += (person["y"] - (obstacle["y"] + obstacle["height"] / 2)) * 0.012
            ax += rng.uniform(-noise, noise)
            ay += rng.uniform(-noise, noise)
            mag = max(0.001, math.hypot(ax, ay))
            person["vx"] = person["vx"] * 0.72 + ax / mag * speed * 0.28
            person["vy"] = person["vy"] * 0.72 + ay / mag * speed * 0.28
            person["x"] = clamp(person["x"] + person["vx"], 8, width + 24)
            person["y"] = clamp(person["y"] + person["vy"], 8, height - 8)
            for ex in exits:
                if person["x"] >= ex["x"] - 6 and ex["y"] <= person["y"] <= ex["y"] + ex["height"]:
                    person["state"] = "evacuated"
                    evacuated_now += 1
                    break
        if evacuated_now and step % 25 == 0:
            events.append(f"step {step}: {evacuated_now} people evacuated")
    return Result(history, state["frames"], events[-12:])


def simulate_traffic(rules: dict[str, Any], seed: int, max_frames: int) -> Result:
    rng = random.Random(seed)
    world = rules["world"]
    width, height, steps, count = world["width"], world["height"], world["steps"], world["agents"]
    traffic = rules["rules"].get("traffic", {})
    signal = rules["rules"].get("signal", {})
    lanes = int(traffic.get("lanes", 4))
    speed_limit = float(traffic.get("speedLimit", 3.5))
    desired_gap = float(traffic.get("desiredGap", 30))
    acceleration = float(traffic.get("acceleration", 0.08))
    brake = float(traffic.get("brake", 0.14))
    signal_x = float(signal.get("position", 0.58)) * width
    green_steps = int(signal.get("greenSteps", 170))
    red_steps = int(signal.get("redSteps", 110))
    cycle = green_steps + red_steps
    cars = [{"x": rng.uniform(0, width), "lane": i % lanes, "speed": rng.uniform(1.4, speed_limit), "color": ["#2775c7", "#1f9d78", "#d48806", "#7357c8", "#d64545"][i % 5]} for i in range(count)]
    stride = frame_stride(steps, max_frames)
    history: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    events: list[str] = []
    flow = 0

    for step in range(steps + 1):
        avg_speed = sum(car["speed"] for car in cars) / max(1, len(cars))
        queued = len([car for car in cars if car["speed"] < 0.3])
        metrics = {"step": step, "avgSpeed": round(avg_speed, 4), "flow": flow, "queued": queued}
        history.append(metrics)
        red = step % cycle > green_steps
        if step % stride == 0 or step == steps:
            items = []
            lane_height = 56
            top = (height - lanes * lane_height) / 2
            for car in cars:
                items.append({"shape": "rect", "x": round(car["x"], 2), "y": round(top + car["lane"] * lane_height + lane_height / 2 - 8, 2), "w": 18, "h": 13, "color": car["color"], "state": "car"})
            frames.append({"step": step, "mode": "traffic", "width": width, "height": height, "items": items, "metrics": metrics, "lanes": lanes, "signalX": signal_x, "red": red})
        if step == steps:
            break
        for lane in range(lanes):
            lane_cars = sorted([car for car in cars if car["lane"] == lane], key=lambda c: c["x"])
            for idx, car in enumerate(lane_cars):
                ahead = lane_cars[(idx + 1) % len(lane_cars)]
                gap = ahead["x"] - car["x"]
                if gap <= 0:
                    gap += width
                target = speed_limit
                if gap < desired_gap:
                    target = max(0.05, ahead["speed"] - brake * (desired_gap - gap))
                if red and car["x"] < signal_x and signal_x - car["x"] < desired_gap * 1.4:
                    target = 0.04
                old_x = car["x"]
                car["speed"] = car["speed"] * (1 - acceleration) + target * acceleration
                car["x"] += car["speed"]
                if old_x < signal_x <= car["x"] and not red:
                    flow += 1
                if car["x"] > width:
                    car["x"] -= width
        if queued > count * 0.28 and step % 80 == 0:
            events.append(f"step {step}: {queued} cars queued")
    return Result(history, frames, events[-12:])


def simulate_market(rules: dict[str, Any], seed: int, max_frames: int) -> Result:
    rng = random.Random(seed)
    world = rules["world"]
    width, height, steps = world["width"], world["height"], world["steps"]
    firms = json.loads(json.dumps(rules.get("firms") or load_presets()["market"]["firms"]))
    for firm in firms:
        firm.update({"share": 1 / len(firms), "demand": 0, "profit": 0, "lastProfit": 0, "direction": 1 if rng.random() > 0.5 else -1})
    demand_rule = rules["rules"].get("demand", {})
    pricing = rules["rules"].get("pricing", {})
    market_size = float(demand_rule.get("marketSize", 1000))
    price_sensitivity = float(demand_rule.get("priceSensitivity", 0.1))
    quality_weight = float(demand_rule.get("qualityWeight", 1.4))
    learning_rate = float(pricing.get("learningRate", 0.018))
    exploration = float(pricing.get("exploration", 0.8))
    floor_margin = float(pricing.get("floorMargin", 3))
    ceiling = float(pricing.get("ceiling", 72))
    stride = frame_stride(steps, max_frames)
    history: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    events: list[str] = []

    for step in range(steps + 1):
        scores = [math.exp(firm["quality"] * quality_weight - firm["price"] * price_sensitivity) for firm in firms]
        score_total = max(0.0001, sum(scores))
        for firm, score in zip(firms, scores):
            firm["share"] = score / score_total
            firm["demand"] = firm["share"] * market_size
            firm["lastProfit"] = firm["profit"]
            firm["profit"] = max(0, firm["price"] - firm["cost"]) * firm["demand"]
        avg_price = sum(firm["price"] for firm in firms) / len(firms)
        total_profit = sum(firm["profit"] for firm in firms)
        concentration = sum(firm["share"] ** 2 for firm in firms)
        metrics = {"step": step, "avgPrice": round(avg_price, 4), "totalProfit": round(total_profit, 4), "concentration": round(concentration, 5)}
        history.append(metrics)
        if step % stride == 0 or step == steps:
            max_profit = max(1, max(firm["profit"] for firm in firms))
            items = []
            bar_w = min(90, (width - 160) / max(1, len(firms)) - 12)
            for index, firm in enumerate(firms):
                h = (firm["profit"] / max_profit) * (height - 170)
                items.append({"shape": "bar", "x": 80 + index * (bar_w + 18), "y": height - 82 - h, "w": bar_w, "h": h, "color": firm.get("color", "#2775c7"), "label": firm["name"], "value": round(firm["price"], 2)})
            frames.append({"step": step, "mode": "market", "width": width, "height": height, "items": items, "metrics": metrics})
        if step == steps:
            break
        for firm in firms:
            if firm["profit"] < firm["lastProfit"]:
                firm["direction"] *= -1
            rivalry_pull = (avg_price - firm["price"]) * 0.012
            nudge = firm["direction"] * learning_rate * firm["price"] + rivalry_pull + rng.uniform(-exploration, exploration) * 0.08
            firm["price"] = clamp(firm["price"] + nudge, firm["cost"] + floor_margin, ceiling)
        if step % 120 == 0:
            events.append(f"step {step}: average price {avg_price:.2f}")
    return Result(history, frames, events[-12:])


def simulate_ecosystem(rules: dict[str, Any], seed: int, max_frames: int) -> Result:
    rng = random.Random(seed)
    world = rules["world"]
    width, height, steps = world["width"], world["height"], world["steps"]
    eco = rules["rules"].get("ecology", {})
    movement = rules["rules"].get("movement", {})
    speed = float(movement.get("speed", 1.2))
    noise = float(movement.get("noise", 0.42))
    plant_growth = float(eco.get("plantGrowth", 0.14))
    graze_distance2 = float(eco.get("grazeDistance", 9)) ** 2
    hunt_distance2 = float(eco.get("huntDistance", 12)) ** 2
    herbivore_reproduce = float(eco.get("herbivoreReproduce", 0.018))
    predator_reproduce = float(eco.get("predatorReproduce", 0.01))
    plants = [{"x": rng.uniform(14, width - 14), "y": rng.uniform(14, height - 14), "state": "plant", "shape": "square", "r": 2.4} for _ in range(int(eco.get("plants", 120)))]

    def creature(kind: str) -> dict[str, Any]:
        angle = rng.random() * math.tau
        return {"x": rng.uniform(20, width - 20), "y": rng.uniform(20, height - 20), "vx": math.cos(angle), "vy": math.sin(angle), "state": kind, "energy": 90 if kind == "predator" else 60, "r": 4.8 if kind == "predator" else 3.8}

    herbivores = [creature("herbivore") for _ in range(int(eco.get("herbivores", 70)))]
    predators = [creature("predator") for _ in range(int(eco.get("predators", 20)))]
    stride = frame_stride(steps, max_frames)
    state = {"frames": [], "width": width, "height": height}
    history: list[dict[str, Any]] = []
    events: list[str] = []

    def move(agent: dict[str, Any], factor: float = 1.0) -> None:
        agent["vx"] += rng.uniform(-noise, noise)
        agent["vy"] += rng.uniform(-noise, noise)
        mag = max(0.001, math.hypot(agent["vx"], agent["vy"]))
        agent["vx"] = agent["vx"] / mag * speed * factor
        agent["vy"] = agent["vy"] / mag * speed * factor
        agent["x"] = clamp(agent["x"] + agent["vx"], 8, width - 8)
        agent["y"] = clamp(agent["y"] + agent["vy"], 8, height - 8)

    for step in range(steps + 1):
        metrics = {"step": step, "plants": len(plants), "herbivores": len(herbivores), "predators": len(predators)}
        history.append(metrics)
        if step % stride == 0 or step == steps:
            capture_particles(step, "ecosystem", plants + herbivores + predators, metrics, state)
        if step == steps:
            break
        if rng.random() < plant_growth and len(plants) < 420:
            plants.append({"x": rng.uniform(14, width - 14), "y": rng.uniform(14, height - 14), "state": "plant", "shape": "square", "r": 2.4})
        births = 0
        deaths = 0
        new_herbivores = []
        for herbivore in herbivores:
            move(herbivore)
            herbivore["energy"] -= 0.18
            plant_index = next((i for i, plant in enumerate(plants) if distance2(herbivore, plant) <= graze_distance2), -1)
            if plant_index >= 0:
                plants.pop(plant_index)
                herbivore["energy"] += 18
                if rng.random() < herbivore_reproduce and len(herbivores) + len(new_herbivores) < 220:
                    child = dict(herbivore)
                    child["x"] = clamp(child["x"] + rng.uniform(-12, 12), 8, width - 8)
                    child["y"] = clamp(child["y"] + rng.uniform(-12, 12), 8, height - 8)
                    child["energy"] = 38
                    new_herbivores.append(child)
                    births += 1
        herbivores.extend(new_herbivores)
        herbivores = [h for h in herbivores if h["energy"] > 0]

        new_predators = []
        for predator in predators:
            move(predator, 1.08)
            predator["energy"] -= 0.24
            if herbivores:
                closest_index = min(range(len(herbivores)), key=lambda i: distance2(predator, herbivores[i]))
                if distance2(predator, herbivores[closest_index]) <= hunt_distance2:
                    herbivores.pop(closest_index)
                    predator["energy"] += 34
                    if rng.random() < predator_reproduce and len(predators) + len(new_predators) < 90:
                        child = dict(predator)
                        child["x"] = clamp(child["x"] + rng.uniform(-10, 10), 8, width - 8)
                        child["y"] = clamp(child["y"] + rng.uniform(-10, 10), 8, height - 8)
                        child["energy"] = 54
                        new_predators.append(child)
                        births += 1
        old_predators = len(predators)
        old_herbivores = len(herbivores)
        predators.extend(new_predators)
        predators = [p for p in predators if p["energy"] > 0]
        deaths += max(0, old_predators + old_herbivores + len(new_predators) - len(predators) - len(herbivores))
        if births and step % 30 == 0:
            events.append(f"step {step}: {births} births")
        if deaths and step % 30 == 0:
            events.append(f"step {step}: {deaths} deaths")
    return Result(history, state["frames"], events[-12:])


def simulate_generic(rules: dict[str, Any], seed: int, max_frames: int) -> Result:
    rng = random.Random(seed)
    world = rules["world"]
    width, height, steps, count = world["width"], world["height"], world["steps"], world["agents"]
    agents = []
    for idx in range(count):
        angle = rng.random() * math.tau
        agents.append({"x": rng.uniform(20, width - 20), "y": rng.uniform(20, height - 20), "vx": math.cos(angle), "vy": math.sin(angle), "state": "agent", "color": ["#2775c7", "#1f9d78", "#d48806", "#7357c8", "#d64545"][idx % 5]})
    stride = frame_stride(steps, max_frames)
    state = {"frames": [], "width": width, "height": height}
    history: list[dict[str, Any]] = []
    for step in range(steps + 1):
        metrics = {"step": step, "agents": len(agents)}
        history.append(metrics)
        if step % stride == 0 or step == steps:
            capture_particles(step, "generic", agents, metrics, state)
        for agent in agents:
            agent["vx"] += rng.uniform(-0.35, 0.35)
            agent["vy"] += rng.uniform(-0.35, 0.35)
            mag = max(0.001, math.hypot(agent["vx"], agent["vy"]))
            agent["vx"] = agent["vx"] / mag * 1.2
            agent["vy"] = agent["vy"] / mag * 1.2
            agent["x"] = clamp(agent["x"] + agent["vx"], 8, width - 8)
            agent["y"] = clamp(agent["y"] + agent["vy"], 8, height - 8)
    return Result(history, state["frames"], ["generic particle fallback"])


def simulate(rules: dict[str, Any], seed: int, max_frames: int) -> Result:
    model = str(rules.get("model", "virus")).lower()
    if model == "virus":
        return simulate_virus(rules, seed, max_frames)
    if model == "evacuation":
        return simulate_evacuation(rules, seed, max_frames)
    if model == "traffic":
        return simulate_traffic(rules, seed, max_frames)
    if model == "market":
        return simulate_market(rules, seed, max_frames)
    if model == "ecosystem":
        return simulate_ecosystem(rules, seed, max_frames)
    return simulate_generic(rules, seed, max_frames)


def csv_columns(rows: list[dict[str, Any]]) -> list[str]:
    columns = ["step"]
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = csv_columns(rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in columns})


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_html(path: Path, rules: dict[str, Any], result: Result, csv_name: str) -> None:
    payload = {
        "rules": rules,
        "history": result.history,
        "frames": result.frames,
        "events": result.events,
        "csvName": csv_name,
    }
    html = HTML_TEMPLATE.replace("__PAYLOAD__", json.dumps(payload))
    path.write_text(html, encoding="utf-8")


def write_notebook(path: Path, rules: dict[str, Any], csv_name: str) -> None:
    notebook = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": [f"# {rules['name']}\n", "\n", "Generated by the simulation-sandbox skill.\n"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["import csv, json\n", "from pathlib import Path\n", "import matplotlib.pyplot as plt\n"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["RULE_SYSTEM = ", json.dumps(rules, indent=2), "\n", "RULE_SYSTEM\n"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": [f"csv_path = Path({csv_name!r})\n", "rows = list(csv.DictReader(csv_path.open()))\n", "for row in rows:\n", "    for key, value in list(row.items()):\n", "        try:\n", "            row[key] = float(value)\n", "        except ValueError:\n", "            pass\n", "rows[:3], rows[-3:]\n"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["keys = [k for k in rows[0].keys() if k != 'step']\n", "fig, ax = plt.subplots(figsize=(9, 4))\n", "for key in keys:\n", "    ax.plot([r['step'] for r in rows], [r[key] for r in rows], label=key, linewidth=2)\n", "ax.set_title(RULE_SYSTEM['name'])\n", "ax.set_xlabel('step')\n", "ax.grid(True, alpha=0.25)\n", "ax.legend()\n", "fig\n"]},
        ],
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}, "language_info": {"name": "python", "pygments_lexer": "ipython3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(notebook, indent=2), encoding="utf-8")


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Simulation Sandbox</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; background: #f5f6f8; color: #1f2933; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; letter-spacing: 0; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 18px; border-bottom: 1px solid #d7dce3; background: #fff; }
    h1 { margin: 0; font-size: 18px; letter-spacing: 0; }
    .sub { margin-top: 3px; color: #647080; font-size: 12px; }
    main { display: grid; grid-template-columns: minmax(420px, 1fr) 340px; gap: 14px; padding: 14px; min-height: calc(100vh - 64px); }
    .panel { border: 1px solid #d7dce3; border-radius: 8px; background: #fff; box-shadow: 0 14px 34px rgba(31,41,51,.08); overflow: hidden; }
    .panelHead { height: 48px; display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 0 14px; border-bottom: 1px solid #d7dce3; font-size: 13px; font-weight: 750; }
    .canvasWrap { height: calc(100vh - 190px); min-height: 460px; padding: 12px; background: #f8fafc; }
    canvas { width: 100%; height: 100%; display: block; border: 1px solid #cfd6df; border-radius: 8px; background: #fff; }
    .controls { display: grid; grid-template-columns: auto auto 1fr auto; gap: 10px; align-items: center; padding: 12px; border-top: 1px solid #d7dce3; }
    button { height: 36px; border: 1px solid #d7dce3; border-radius: 7px; padding: 0 12px; background: #fff; color: #1f2933; font: inherit; font-weight: 650; cursor: pointer; }
    button.primary { border-color: #1f9d78; background: #1f9d78; color: #fff; }
    input { width: 100%; accent-color: #1f9d78; }
    .side { padding: 14px; overflow: auto; max-height: calc(100vh - 92px); }
    .metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
    .metric { border: 1px solid #d7dce3; border-radius: 8px; padding: 10px; background: #fbfcfd; min-width: 0; }
    .label { color: #647080; font-size: 11px; font-weight: 750; text-transform: uppercase; overflow-wrap: anywhere; }
    .value { margin-top: 6px; font-size: 24px; line-height: 1.05; font-weight: 800; overflow-wrap: anywhere; }
    #chart { height: 210px; margin-bottom: 14px; }
    pre { margin: 0; border: 1px solid #d7dce3; border-radius: 8px; padding: 10px; background: #fbfcfd; color: #42505f; overflow: auto; font-size: 12px; }
    .actions { display: grid; gap: 8px; margin-top: 14px; }
    @media (max-width: 880px) { main { grid-template-columns: 1fr; } .canvasWrap { height: 58vh; } .side { max-height: none; } }
  </style>
</head>
<body>
  <header>
    <div><h1 id="title">Simulation Sandbox</h1><div class="sub" id="description"></div></div>
    <div id="stepReadout">step 0</div>
  </header>
  <main>
    <section class="panel">
      <div class="panelHead"><span id="model"></span><span id="status">paused</span></div>
      <div class="canvasWrap"><canvas id="world"></canvas></div>
      <div class="controls">
        <button id="run" class="primary">Run</button>
        <button id="step">Step</button>
        <input id="speed" type="range" min="1" max="12" value="4" aria-label="Speed">
        <button id="csv">CSV</button>
      </div>
    </section>
    <aside class="panel side">
      <div class="metrics" id="metrics"></div>
      <canvas id="chart"></canvas>
      <pre id="events"></pre>
      <div class="actions"><button id="rules">Download Rules</button></div>
    </aside>
  </main>
  <script>
    const PAYLOAD = __PAYLOAD__;
    const FRAMES = PAYLOAD.frames;
    const HISTORY = PAYLOAD.history;
    const RULES = PAYLOAD.rules;
    const world = document.getElementById("world");
    const chart = document.getElementById("chart");
    const ctx = world.getContext("2d");
    const chartCtx = chart.getContext("2d");
    let index = 0;
    let running = false;

    document.getElementById("title").textContent = RULES.name || "Simulation Sandbox";
    document.getElementById("description").textContent = RULES.description || "Generated simulation replay";
    document.getElementById("model").textContent = RULES.model || "model";
    document.getElementById("events").textContent = PAYLOAD.events.length ? PAYLOAD.events.join("\n") : "no notable events";

    function fit(canvas) {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
      const w = Math.max(320, Math.floor(rect.width * dpr));
      const h = Math.max(220, Math.floor(rect.height * dpr));
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    }
    function drawFrame() {
      fit(world);
      const frame = FRAMES[index] || FRAMES[0];
      const sx = world.width / frame.width;
      const sy = world.height / frame.height;
      ctx.clearRect(0, 0, world.width, world.height);
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, world.width, world.height);
      if (frame.mode === "traffic") drawTrafficBackground(frame, sx, sy);
      if (frame.mode === "evacuation") drawEvacuationBackground(frame, sx, sy);
      if (frame.mode === "market") drawMarketAxes(frame, sx, sy);
      for (const item of frame.items) {
        ctx.fillStyle = item.color || "#2775c7";
        if (item.shape === "rect") ctx.fillRect(item.x * sx, item.y * sy, item.w * sx, item.h * sy);
        else if (item.shape === "bar") {
          ctx.globalAlpha = .9;
          ctx.fillRect(item.x * sx, item.y * sy, item.w * sx, item.h * sy);
          ctx.globalAlpha = 1;
          ctx.fillStyle = "#1f2933";
          ctx.font = `${Math.max(10, 12 * sy)}px sans-serif`;
          ctx.fillText(item.label || "", item.x * sx, (frame.height - 46) * sy);
          ctx.fillStyle = "#647080";
          ctx.fillText(String(item.value ?? ""), item.x * sx, (frame.height - 28) * sy);
        } else if (item.shape === "square") {
          const r = Math.max(1.5, item.r * sx);
          ctx.fillRect(item.x * sx - r, item.y * sy - r, r * 2, r * 2);
        } else {
          ctx.beginPath();
          ctx.arc(item.x * sx, item.y * sy, Math.max(2, item.r * sx), 0, Math.PI * 2);
          ctx.fill();
        }
      }
      document.getElementById("stepReadout").textContent = `step ${frame.step}`;
      drawMetrics(frame.metrics || {});
      drawChart();
    }
    function drawTrafficBackground(frame, sx, sy) {
      const lanes = frame.lanes || 4;
      const laneHeight = 56;
      const top = (frame.height - lanes * laneHeight) / 2;
      ctx.fillStyle = "#495463";
      ctx.fillRect(0, (top - 14) * sy, frame.width * sx, (lanes * laneHeight + 28) * sy);
      ctx.strokeStyle = "rgba(255,255,255,.45)";
      ctx.setLineDash([12 * sx, 10 * sx]);
      for (let lane = 1; lane < lanes; lane++) {
        const y = (top + lane * laneHeight) * sy;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(frame.width * sx, y); ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.strokeStyle = frame.red ? "#d64545" : "#1f9d78";
      ctx.lineWidth = 5;
      ctx.beginPath(); ctx.moveTo(frame.signalX * sx, (top - 28) * sy); ctx.lineTo(frame.signalX * sx, (top + lanes * laneHeight + 28) * sy); ctx.stroke();
      ctx.lineWidth = 1;
    }
    function drawEvacuationBackground(frame, sx, sy) {
      ctx.strokeStyle = "#aab4c0";
      ctx.strokeRect(6 * sx, 6 * sy, (frame.width - 12) * sx, (frame.height - 12) * sy);
      for (const ex of frame.exits || []) {
        ctx.fillStyle = "#1f9d78";
        ctx.fillRect(ex.x * sx, ex.y * sy, ex.width * sx, ex.height * sy);
      }
      for (const ob of frame.obstacles || []) {
        ctx.fillStyle = "#e7eaee";
        ctx.strokeStyle = "#aeb7c1";
        ctx.fillRect(ob.x * sx, ob.y * sy, ob.width * sx, ob.height * sy);
        ctx.strokeRect(ob.x * sx, ob.y * sy, ob.width * sx, ob.height * sy);
      }
    }
    function drawMarketAxes(frame, sx, sy) {
      ctx.strokeStyle = "#cfd6df";
      ctx.beginPath();
      ctx.moveTo(70 * sx, 45 * sy);
      ctx.lineTo(70 * sx, (frame.height - 82) * sy);
      ctx.lineTo((frame.width - 42) * sx, (frame.height - 82) * sy);
      ctx.stroke();
    }
    function drawMetrics(metrics) {
      const box = document.getElementById("metrics");
      box.innerHTML = "";
      Object.entries(metrics).filter(([k]) => k !== "step").forEach(([key, value]) => {
        const node = document.createElement("div");
        node.className = "metric";
        node.innerHTML = `<div class="label">${key}</div><div class="value">${typeof value === "number" ? Number(value).toFixed(Number.isInteger(value) ? 0 : 2) : value}</div>`;
        box.append(node);
      });
    }
    function drawChart() {
      fit(chart);
      const keys = Object.keys(HISTORY[0] || {}).filter(k => k !== "step");
      chartCtx.clearRect(0, 0, chart.width, chart.height);
      chartCtx.fillStyle = "#fff"; chartCtx.fillRect(0, 0, chart.width, chart.height);
      const pad = 32;
      let max = 1;
      for (const row of HISTORY) for (const key of keys) max = Math.max(max, Number(row[key]) || 0);
      const colors = ["#2775c7", "#d64545", "#1f9d78", "#d48806", "#7357c8", "#647080"];
      chartCtx.strokeStyle = "#d7dce3";
      chartCtx.beginPath(); chartCtx.moveTo(pad, 12); chartCtx.lineTo(pad, chart.height - pad); chartCtx.lineTo(chart.width - 8, chart.height - pad); chartCtx.stroke();
      keys.forEach((key, ki) => {
        chartCtx.strokeStyle = colors[ki % colors.length];
        chartCtx.lineWidth = 2;
        chartCtx.beginPath();
        HISTORY.forEach((row, i) => {
          const x = pad + i / Math.max(1, HISTORY.length - 1) * (chart.width - pad - 12);
          const y = chart.height - pad - (Number(row[key]) || 0) / max * (chart.height - pad - 18);
          if (i === 0) chartCtx.moveTo(x, y); else chartCtx.lineTo(x, y);
        });
        chartCtx.stroke();
      });
    }
    function csvText() {
      const cols = Object.keys(HISTORY[0] || {});
      return [cols.join(","), ...HISTORY.map(row => cols.map(c => row[c] ?? "").join(","))].join("\n");
    }
    function download(name, text, type) {
      const url = URL.createObjectURL(new Blob([text], {type}));
      const a = document.createElement("a");
      a.href = url; a.download = name; document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 400);
    }
    document.getElementById("run").onclick = () => { running = !running; document.getElementById("run").textContent = running ? "Pause" : "Run"; document.getElementById("status").textContent = running ? "running" : "paused"; };
    document.getElementById("step").onclick = () => { index = Math.min(FRAMES.length - 1, index + 1); drawFrame(); };
    document.getElementById("csv").onclick = () => download(PAYLOAD.csvName || "simulation-results.csv", csvText(), "text/csv");
    document.getElementById("rules").onclick = () => download("rule-system.json", JSON.stringify(RULES, null, 2), "application/json");
    window.onresize = drawFrame;
    function loop() {
      if (running) index = Math.min(FRAMES.length - 1, index + Number(document.getElementById("speed").value || 4));
      if (index >= FRAMES.length - 1) running = false;
      drawFrame();
      requestAnimationFrame(loop);
    }
    loop();
  </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate simulation sandbox artifacts.")
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--rules", type=Path, help="Path to rule-system JSON.")
    source.add_argument("--preset", choices=sorted(load_presets().keys()), help="Built-in preset to use.")
    parser.add_argument("--out", type=Path, required=True, help="Output directory.")
    parser.add_argument("--formats", default="html,csv,notebook,json", help="Comma-separated outputs: html,csv,notebook,json.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument("--steps", type=int, help="Override rule-system step count.")
    parser.add_argument("--max-frames", type=int, default=180, help="Maximum replay frames embedded in HTML.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    presets = load_presets()
    raw_rules = load_json(args.rules) if args.rules else presets[args.preset or "virus"]
    rules = normalize_rules(raw_rules, args.steps)
    result = simulate(rules, args.seed, args.max_frames)
    args.out.mkdir(parents=True, exist_ok=True)
    base = slugify(rules["name"])
    formats = {fmt.strip().lower() for fmt in args.formats.split(",") if fmt.strip()}
    written: dict[str, str] = {}
    csv_name = f"{base}.csv"
    if "csv" in formats or "html" in formats or "notebook" in formats:
        csv_path = args.out / csv_name
        write_csv(csv_path, result.history)
        written["csv"] = str(csv_path)
    if "json" in formats:
        json_path = args.out / f"{base}-rules.json"
        write_json(json_path, rules)
        written["json"] = str(json_path)
    if "html" in formats:
        html_path = args.out / f"{base}.html"
        write_html(html_path, rules, result, csv_name)
        written["html"] = str(html_path)
    if "notebook" in formats:
        notebook_path = args.out / f"{base}.ipynb"
        write_notebook(notebook_path, rules, csv_name)
        written["notebook"] = str(notebook_path)
    print(json.dumps({"name": rules["name"], "model": rules["model"], "steps": rules["world"]["steps"], "seed": args.seed, "written": written}, indent=2))


if __name__ == "__main__":
    main()
