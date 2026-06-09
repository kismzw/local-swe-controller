# Script Workflow Fixture

This repository keeps a small script chain:

1. Build dataset artifacts in `DataPipe/`
2. Train or test downstream models in `DownStream/`

```bash
python DataPipe/Build_tiles_dataset.py --help
python DownStream/ROI_MultiRegression/MultiRegression_Train.py --help
```
