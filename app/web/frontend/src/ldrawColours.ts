// The most common LDraw colour codes with their usual RGB; anything else falls back to grey.
const HEX: Record<number, string> = {
  0: "#05131D", 1: "#0055BF", 2: "#237841", 3: "#008F9B", 4: "#C91A09", 5: "#C870A0", 6: "#583927", 7: "#9BA19D",
  8: "#6D6E5C", 9: "#B4D2E3", 10: "#4B9F4A", 11: "#55A5AF", 14: "#F2CD37", 15: "#FFFFFF", 17: "#C2DAB8",
  18: "#FBE696", 19: "#E4CD9E", 20: "#C9CAE2", 22: "#81007B", 25: "#FE8A18", 26: "#923978", 27: "#BBE90B",
  28: "#958A73", 29: "#E4ADC8", 30: "#AC78BA", 31: "#CDA4DE", 33: "#0020A0", 34: "#237841", 36: "#C91A09",
  40: "#635F52", 46: "#F5CD2A", 47: "#FCFCFC", 57: "#F08F1C", 70: "#582A12", 71: "#A0A5A9", 72: "#6C6E68",
  73: "#5A93DB", 74: "#73DCA1", 77: "#FECCCF", 78: "#F6D7B3", 84: "#AA7D55", 85: "#3F3691", 89: "#4C61DB",
  92: "#BB805A", 272: "#0A3463", 288: "#184632", 308: "#352100", 320: "#720E0F", 321: "#078BC9", 322: "#36AEBF",
  323: "#D3F2EA", 330: "#77774E", 378: "#A0BCAC", 379: "#6074A1", 484: "#A95500",
};

export const colourHex = (code: number): string => HEX[code] ?? "#8A8A8A";
