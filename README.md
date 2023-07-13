# Simple linux RAPL reader 

Easily measure the power consumption of a linux system

## Features

Automatically discover all RAPL domains on a linux system\
All RAPL measures are converted to Watts\
Monitor global CPU usage jointly with package consumption\
Monitor per-socket CPU usage jointly with their consumtion (useful on a multi-socket system)\
Measures are formatted on a ready to use CSV (under normalised timestamp keys)\
Live display is also possible with ```--live``` option\

## Usage

```bash
python3 rapl-reader.py --help
```

/!\ RAPL access may require root rights

To dump on default ```consumption.csv``` while also displaying measures to the console
```bash
python3 rapl-reader.py --live
```

To change default values:
```bash
python3 rapl-reader.py --delay=(sec) --precision=(number of digits) --output=consumption.csv
```
