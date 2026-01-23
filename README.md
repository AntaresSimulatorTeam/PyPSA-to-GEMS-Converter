<div align="center">
    <b>PyPSA to GEMS Converter</b> 
</div>

## About 
The PyPSA to GEMS Converter is an open-source tool that enables the conversion of studies conducted in PyPSA into the GEMS format.


### Key Features 
- **Conversion of linear optimal power flow studies**
- **Conversion of two-stage stochastic optimization studies**

## Table of Contents
- [How the Converter Works](#how-the-converter-works)
- [Input and Output of the Converter](#input-and-output-of-the-converter)
- [Current Limitations of the Converter](#current-limitations-of-the-converter)
- [Step-by-Step Guide: Manually Executing a Simulation in GEMS Modeler](#step-by-step-guide-manually-executing-a-simulation-in-gems-modeler)
- [Comparing Results Between GEMS Modeler and PyPSA](#comparing-results-between-gems-modeler-and-pypsa)
- [Pros and Cons](#pros-and-cons)


## How the Converter Works

The PyPSA to GEMS Converter transforms PyPSA network models into GEMS-compatible studies through a multi-stage conversion pipeline. 
### 1. **Input Validation and Preprocessing**
The converter first validates that the PyPSA network meets the requirements for conversion.<br/>
It performs necessary preprocessing steps such as normalizing component names, handling missing attributes, and ensuring data consistency.<br/>
This stage ensures the input PyPSA model is **compatible** with the conversion process.<br/>

### 2. **Component Registration and Data Extraction**
The converter identifies and extracts all relevant components from the PyPSA network, including both **static (constant)** and **dynamic (time-dependent)** parameters. <br/>
It maps PyPSA-specific parameter names to their GEMS equivalents and organizes the data for conversion.

### 3. **Time Series Processing**
For parameters that vary over time, the converter extracts time series data and writes them to separate data files (**CSV** or **TSV** format).<br/> 
The converter handles both deterministic studies (single time series) and stochastic studies (multiple scenarios), maintaining the temporal structure of the original PyPSA model.

### 4. **GEMS Component Generation**
Each PyPSA component is transformed into its corresponding GEMS representation. <br/>
The converter creates GEMS components with appropriate parameters, distinguishing between constant values and time-dependent references. <br/>
Connections between components (such as generators and loads connected to buses) are established through GEMS port connections. <br/>

### 5. **Global Constraints Handling**
If the PyPSA model includes global constraints (such as CO₂ emission limits), the converter identifies these and creates corresponding GEMS constraint components, linking them to the relevant system components.

### 6. **Study Structure Generation**
Finally, the converter generates the complete GEMS study structure.

## Input and Output of the Converter

**Input:**
The converter requires the following inputs: <br/> 
- **PyPSA network object** <br/>
The fully defined PyPSA network that will be converted into a GEMS-compatible study.<br/>
- **Logger** <br/>
Used for debugging and tracing the conversion process. Logs can help identify configuration issues or data inconsistencies during conversion.<br/> 
- **Output path** <br/>
The directory where the generated GEMS study will be created.<br/>
- **Time series file format** <br/>
Format used for exported time-dependent data (e.g. csv, tsv).

## Output
The converter generates a **structured GEMS study directory** at the provided output path. <br/>
The directory layout follows the conventions expected by the GEMS modeler: <br/>

```md
    study_directory/
    └── systems/
        └── system_name/
            └── input/
                ├── optim-config.yml--------> Benders decomposition parameters, used by the modeler to generate MPS files
                ├── system.yml -------------> Main system description
                ├── parameters.yml----------> Solver and simulation parameters
                ├── model-libraries/ 
                │   └── pypsa_models.yml---> Model library definitions 
                └── data-series/ ----------> Time and/or scenarion dependent parameters
                    └── ...
```

## Current Limitations of the Converter

### Unsupported Components
- Lines (not implemented)
- Transformers (not implemented)
## Component Restrictions

### Generators
- **`active = 1`** - All generators are included in the optimization.
- **`marginal_cost_quadratic = 0`** - Only linear generation costs are supported.
- **`committable = False`** - Unit commitment (on/off decisions) is not supported.

### Loads
- **`active = 1`** - All loads are fixed and always active.

### Links
- **`active = 1`** - All links are always active.

### Storage Units
- **`active = 1`** - All storage units are included in the optimization.
- **`sign = 1`** - Storage operates with positive dispatch direction.
- **`cyclic_state_of_charge = 1`** - End state of charge must equal the initial state.
- **`marginal_cost_quadratic = 0`** - Only linear storage costs are supported.

### Stores
- **`active = 1`** - All stores are included in the optimization.
- **`sign = 1`**  - Store energy flows are positive.
- **`e_cyclic = 1`** - End energy level must equal the initial level.
- **`marginal_cost_quadratic = 0`** - Only linear storage costs are supported.

### Global Constraints
- **`type = primary_energy`** - Only primary energy constraints are supported.
- **`carrier.co2_emissions`** - CO₂ accounting must be defined at the carrier level.
- **Supported senses:** `<=`, `==`
## Step-by-Step Guide: Manually Executing a Simulation in GEMS Modeler
- Build or load a PyPSA network 
```python
# Setup
logger = logging.getLogger(__name__)
study_dir = Path("tmp/my_study")  # Absolute path to the GEMS study directory

# Option A: build the network in code
network = Network()
network.add("Bus", "bus1", v_nom=1)
network.add("Load", "load1", bus="bus1", p_set=[10, 20, 30])
network.add("Generator", "gen1", bus="bus1", p_nom=100, marginal_cost=50)

# Option B: load the network from a file
network = Network("simple_network.nc")  # Absolute path to the PyPSA file
```
- Convert the PyPSA network to a GEMS study
```python
# Convert PyPSA network to GEMS
converter = PyPSAStudyConverter(
    pypsa_network=network,
    logger=logger,
    study_dir=study_dir,
    series_file_format=".tsv",  # Supported formats: .tsv, .csv
).to_gems_study()
```
- Run the GEMS(Antares) optimization 
```python
# Path to the Antares modeler binary
modeler_bin = Path("antares-9.3.5-Ubuntu-22.04/bin/antares-modeler")

# Run the optimization
subprocess.run([
    str(modeler_bin),
    str(study_dir / "systems")
])
```
## Comparing Results Between GEMS Modeler and PyPSA
If you want to see detailed statistics and a comparison between **Antares Modeler** and **PyPSA study optimization**, you can check the full analysis here:  

👉 [View benchmark analysis](https://github.com/AntaresSimulatorTeam/PyPSA-to-GEMS-Converter/blob/converter-docs/src/tests/local_benchmark/benchmark_analysis.ipynb)

## Pros and Cons

### Pros:
  - Converts PyPSA networks to GEMS (LOPF and two-stage stochastic)
  - Simple API, flexible time series, input validation, automatic preprocessing
### Cons:
  - Limited components: no lines/transformers, no unit commitment, only linear costs
  - Limited constraints: no investment periods, uniform snapshot weights, few global constraints
  - Execution: no Python API to directly run Antares Modeler or Xpansion refactor

