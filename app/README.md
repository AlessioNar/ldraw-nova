# ldraw-nova app

> [!IMPORTANT]
> **Want to try the ldraw-nova web app? Start at the [installation steps](../README.md#installation).**
>
> This directory is the Docker packaging of the ldraw-nova toolkit at the repo root. It turns that toolset into a web app you can run; the image is built from both, with the repo root passed as the `nova` build context.
>
> The project's landing page, demo video and installation steps are in the [top-level README](../README.md). This README will only cover the technical side of the Docker image, and it's being rewritten.

## Overview

COMING SOON

## Build

COMING SOON

## Configuration

COMING SOON

## Development

CI (`.github/workflows/ci.yml`) builds the frontend, then builds this image and runs the smoke checks, the backend tests and the root tests inside it, on every pull request and push to `master`. That is the only place the tests needing LeoCAD and the baked LDraw library run automatically.

## Acknowledgements

COMING SOON