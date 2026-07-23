# Simulation Sandbox DSL

Use this JSON shape as the interchange format between a user idea and generated simulator artifacts.

```json
{
  "name": "Campus Flu Spread",
  "model": "virus",
  "description": "mobile agents, contact infection, probabilistic recovery",
  "world": {
    "width": 960,
    "height": 640,
    "agents": 360,
    "steps": 900
  },
  "rules": {}
}
```

## Core Fields

- `name`: Human-readable experiment name.
- `model`: One of `virus`, `evacuation`, `traffic`, `market`, `ecosystem`, or a custom model name.
- `description`: Short model intent.
- `world.width`, `world.height`: Canvas coordinate space.
- `world.agents`: Initial agent count where relevant.
- `world.steps`: Number of simulation ticks.
- `rules`: Model-specific parameters.

## Supported Model Rules

### virus

Optional `agents` array:

```json
[
  {"type": "susceptible", "count": 342, "color": "#2775c7"},
  {"type": "infected", "count": 18, "color": "#d64545"},
  {"type": "recovered", "count": 0, "color": "#1f9d78"}
]
```

Rules:

- `movement.speed`: Agent step speed.
- `movement.noise`: Random direction perturbation.
- `infection.distance`: Contact radius.
- `infection.probability`: Infection probability per close contact per tick.
- `recovery.afterSteps`: Minimum infected age before recovery.
- `recovery.probability`: Recovery probability per tick after the threshold.

### evacuation

Rules:

- `movement.speed`: Agent movement speed.
- `movement.noise`: Random jitter.
- `movement.separation`: Desired personal space radius.
- `evacuation.exits`: Array of rectangles `{x,y,width,height}`.
- `evacuation.obstacles`: Array of rectangles `{x,y,width,height}`.

### traffic

Rules:

- `traffic.lanes`: Number of lanes.
- `traffic.speedLimit`: Max vehicle speed.
- `traffic.desiredGap`: Following distance.
- `traffic.acceleration`: Blend factor toward target speed.
- `traffic.brake`: Braking pressure when gap is small.
- `signal.position`: Fractional signal location along road, 0 to 1.
- `signal.greenSteps`: Green phase duration.
- `signal.redSteps`: Red phase duration.

### market

Use `firms`:

```json
[
  {"name": "Aster", "price": 34, "cost": 15, "quality": 1.05, "color": "#2775c7"}
]
```

Rules:

- `demand.marketSize`: Total demand pool.
- `demand.priceSensitivity`: Demand penalty per price unit.
- `demand.qualityWeight`: Demand reward per quality unit.
- `pricing.learningRate`: Price adjustment rate.
- `pricing.exploration`: Random price-search amplitude.
- `pricing.floorMargin`: Minimum margin over cost.
- `pricing.ceiling`: Maximum price.

### ecosystem

Rules:

- `ecology.plants`, `ecology.herbivores`, `ecology.predators`: Initial populations.
- `ecology.plantGrowth`: New plant probability per tick.
- `ecology.grazeDistance`: Herbivore-plant interaction radius.
- `ecology.huntDistance`: Predator-herbivore interaction radius.
- `ecology.herbivoreReproduce`, `ecology.predatorReproduce`: Reproduction probabilities after feeding.
- `movement.speed`, `movement.noise`: Creature movement.

## Modeling Conventions

- Keep probabilities between 0 and 1.
- Keep counts small enough for an interactive browser replay unless the user asks for batch simulation.
- Use seeded randomness for reproducible experiments.
- Treat outputs as exploratory unless calibrated with real data.
