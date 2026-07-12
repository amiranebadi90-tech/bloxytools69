import { getConfig } from "../utils/configUtils.js";
function getCoordinates(type) {
  const configValues = getConfigValues();
  if (!configValues) {
    throw new Error("Couldn't fetch config - coordinates.");
  }
  const { scalingFactor, xpPercent } = configValues;
  const guiCoordinates = {
    twoSlots: {
      x: 1,
      y: 1,
      width: 40 * scalingFactor,
      height: 20 * scalingFactor
    },
    lastSlot: {
      x: 161 * scalingFactor,
      y: 1,
      width: 20 * scalingFactor,
      height: 20 * scalingFactor
    },
    selector: {
      x: 1 * scalingFactor,
      y: 23 * scalingFactor,
      width: 22 * scalingFactor,
      height: 22 * scalingFactor
    }
  };
  const xpAdjustmentFactor = guiCoordinates.twoSlots.width + guiCoordinates.lastSlot.width;
  const iconCoordinates = {
    xpBg: {
      x: 182 * scalingFactor - xpAdjustmentFactor,
      y: 64 * scalingFactor,
      width: xpAdjustmentFactor,
      height: 5 * scalingFactor
    },
    hungerBg: {
      x: 16 * scalingFactor,
      y: 27 * scalingFactor,
      width: 9 * scalingFactor,
      height: 9 * scalingFactor
    },
    heartBg: {
      x: 16 * scalingFactor,
      y: 0 * scalingFactor,
      width: 9 * scalingFactor,
      height: 9 * scalingFactor
    },
    xp: {
      x: 0 * scalingFactor,
      y: 69 * scalingFactor,
      width: xpAdjustmentFactor * xpPercent,
      height: 5 * scalingFactor
    },
    hunger: {
      x: 52 * scalingFactor,
      y: 27 * scalingFactor,
      width: 9 * scalingFactor,
      height: 9 * scalingFactor
    },
    heart: {
      x: 52 * scalingFactor,
      y: 0 * scalingFactor,
      width: 9 * scalingFactor,
      height: 9 * scalingFactor
    },
    armor: {
      x: 34 * scalingFactor,
      y: 9 * scalingFactor,
      width: 9 * scalingFactor,
      height: 9 * scalingFactor
    }
  };
  switch (type) {
    case "ICON": {
      return iconCoordinates;
    }
    case "GUI": {
      return guiCoordinates;
    }
    default: {
      throw new Error(`Invalid type: ${type}`);
    }
  }
}
function getDestinationCoordinates(type) {
  const configValues = getConfigValues();
  if (!configValues) {
    throw new Error("Couldn't fetch config - coordinates.");
  }
  const { scalingFactor, xpPercent } = configValues;
  const xpBarHeight = 5 * scalingFactor;
  const bottomOffset = (offset) => (22 - offset) * scalingFactor;
  const twoSlots = {
    x: 0,
    y: bottomOffset(20 + 1),
    // offset for y-position based on scalingFactor (20px for it's height and 1px to center it)
    width: 40 * scalingFactor,
    height: 20 * scalingFactor
  };
  const lastSlot = {
    x: twoSlots.width,
    y: bottomOffset(20 + 1),
    // same y-position as twoSlots
    width: 20 * scalingFactor,
    height: 20 * scalingFactor
  };
  const selector = {
    x: lastSlot.width - scalingFactor,
    y: bottomOffset(22),
    // slightly above twoSlots and lastSlot (this is where the 2px from those balance)
    width: 22 * scalingFactor,
    height: 22 * scalingFactor
  };
  const guiCoordinates = {
    twoSlots,
    lastSlot,
    selector
  };
  if (type === "GUI") {
    return guiCoordinates;
  }
  const guiWidth = guiCoordinates.twoSlots.width + guiCoordinates.lastSlot.width;
  const armor = {
    x: 0,
    y: 0,
    width: 9 * scalingFactor,
    height: 9 * scalingFactor
  };
  const heartBase = {
    x: 0,
    y: 9 * scalingFactor + 1 * scalingFactor,
    width: armor.width,
    height: armor.height
  };
  const heart = { ...heartBase };
  const heartBg = { ...heartBase };
  const hungerBase = {
    x: guiWidth - heartBase.width * 3,
    y: heartBase.y,
    width: heartBase.width,
    height: heartBase.height
  };
  const hunger = { ...hungerBase };
  const hungerBg = { ...hungerBase };
  const xpBg = {
    x: 0,
    y: heartBase.height + armor.height + 2 * scalingFactor,
    width: guiWidth,
    height: xpBarHeight
  };
  const xp = {
    x: 0,
    y: xpBg.y,
    width: guiWidth * xpPercent,
    height: xpBarHeight
  };
  const iconCoordinates = {
    xpBg,
    hungerBg,
    heartBg,
    xp,
    hunger,
    heart,
    armor
  };
  if (type === "ICON") {
    return iconCoordinates;
  }
  throw new Error("Invalid Type!");
}
function getConfigValues() {
  try {
    const config = getConfig();
    const scalingFactor = config.scalingFactor;
    const xpPercent = config.xpPercent;
    return { scalingFactor, xpPercent };
  } catch (err) {
    console.error("Error fetching config: ", err);
    return void 0;
  }
}
export {
  getCoordinates,
  getDestinationCoordinates
};
