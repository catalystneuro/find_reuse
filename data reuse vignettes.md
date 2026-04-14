
TOOL_DEMO — The paper introduces a new analysis tool, software package, or processing pipeline and uses the open dataset as example data to demonstrate its capabilities. The scientific question is secondary to showcasing the method.
                                                                                                                         
NOVEL_ANALYSIS — The paper applies a new scientific question or analytical approach to the existing dataset that the original authors did not explore. The focus is on generating new scientific insight from previously collected data.
                                                                                                                         
AGGREGATION — The paper combines this dataset with other datasets to increase statistical power, perform cross-dataset comparisons, or build a larger pooled analysis or database.
                                                                                                                         
BENCHMARK — The dataset is used as a standard reference to evaluate and compare the performance of algorithms, decoders, or models against other methods.
                                                                                                                         
CONFIRMATORY — The paper has its own novel data or experiments and uses the open dataset as independent evidence to replicate, validate, or corroborate their primary findings.
                                                                                                                         
SIMULATION — The paper uses the real data to validate, constrain, or parameterize a computational model or simulation, ensuring the model produces realistic outputs.
                                                                                                                         
ML_TRAINING — The dataset is primarily used as training data for a machine learning or deep learning model, such as a neural network, foundation model, or classifier.
                                                                                                                         
TEACHING — The dataset is used for educational purposes, including tutorials, online courses, textbooks, or workshop exercises.

================================================================================
 TOOL_DEMO
================================================================================

 DOI: 10.7554/elife.95802.2
 Dandiset: 000351
 Confidence: 10
 Reasoning: The authors explicitly state they are using the dataset to 'demonstrate how our method (FLMM) can be used
to answer scientific questions' and to 'illustrate how to test this question in our framework.' The focus is on
showcasing a new statistical tool using the existing dataset as a practical example.

 DOI: 10.7554/elife.59928
 Dandiset: 000023
 Confidence: 10
 Reasoning: The primary goal of the paper is to introduce and demonstrate a new standardized nomenclature schema for
cell types. The authors explicitly use the DANDI/Hodge et al. dataset as a 'Use Case' to show how their proposed
naming system and infrastructure can be applied to existing taxonomies.

================================================================================
 NOVEL_ANALYSIS
================================================================================

 DOI: 10.1523/jneurosci.0398-23.2023
 Dandiset: 000575
 Confidence: 10
 Reasoning: The authors are re-analyzing a dataset they previously collected and published (Boran et al., 2022) to
address a new scientific question. While the original paper focused on persistent firing in the entorhinal cortex,
this paper investigates the interaction between the entorhinal cortex and the hippocampus during working memory
maintenance. This fits the definition of asking a new scientific question of an existing dataset.

 DOI: 10.1038/s41598-025-21967-7
 Dandiset: 000004
 Confidence: 10
 Reasoning: The authors used preprocessed spike counts and behavioral data from an existing dataset to carry out
their own 'planned analyses' to answer a scientific question. There is no mention of developing a new tool,
benchmarking, or combining it with their own new experimental data; they are performing a secondary analysis of an
existing open dataset to gain new scientific insights.

================================================================================
 AGGREGATION
================================================================================

 DOI: 10.1093/nar/gkae911
 Dandiset: 000768
 Confidence: 10
 Reasoning: The citing paper describes MAPbrain, a database specifically designed to integrate, normalize, and host
multiple publicly available multi-omics datasets from primates into a single platform for cross-dataset exploration.

 DOI: 10.64898/2025.12.05.692533
 Dandiset: 001464
 Confidence: 10
 Reasoning: The paper explicitly describes building a database by pooling 388 datasets from 36 different studies to
perform a large-scale meta-analysis. This is a classic case of data aggregation to increase statistical power for
cross-dataset comparisons.

================================================================================
 BENCHMARK
================================================================================

 DOI: 10.3390/e24020152
 Dandiset: 000128
 Confidence: 10
 Reasoning: The authors explicitly state they used the MC_Maze dataset from the Neural Latents Benchmark (NLB) to
compare the performance of their VDGLVM model against other methods across different dataset scales (L, M, S).

 DOI: 10.1101/2025.10.17.683009
 Dandiset: 000569
 Confidence: 10
 Reasoning: The citing paper develops a new gene homology mapping framework and uses the dataset (referred to as
'Gra') as a standard reference to compare the performance of their method (HL_O2O) against existing strategies
(ENS_M2M) in cross-species integration tasks.

================================================================================
 CONFIRMATORY
================================================================================

 DOI: 10.1101/2025.05.02.651955
 Dandiset: 000149
 Confidence: 10
 Reasoning: The authors explicitly state they used the IBL dataset to replicate and complement their own experimental
findings, which is the definition of the CONFIRMATORY category.

 DOI: 10.1101/2025.01.16.633450
 Dandiset: 000402
 Confidence: 10
 Reasoning: The authors used the MICrONS dataset to validate a specific finding from their own primary experimental
data. As stated in Excerpt 2, they 'confirmed this correlation of inputs and outputs by quantifying the connectivity
of basket cells' in the external EM reconstruction, using it as a secondary evidence source to support their main
discovery.

================================================================================
 SIMULATION
================================================================================

 DOI: 10.1101/2022.07.14.500004
 Dandiset: 000008
 Confidence: 10
 Reasoning: The authors used the morphological reconstructions from the dataset to parameterize and constrain a
multi-compartment biophysical simulation in NEURON. The goal was to test how spatial structure affects synaptic
integration compared to point-neuron models.

 DOI: 10.1371/journal.pcbi.1013818
 Dandiset: 000776
 Confidence: 10
 Reasoning: The authors explicitly state they used the calcium imaging data to fit model parameters, constrain the
selection of neurons, and ensure the model's dynamical outputs are 'realistic' and 'similar to data.' The primary goal
is to build and validate a biophysical dynamical systems model using the dataset as the ground truth for simulation
parameters.

================================================================================
 ML_TRAINING
================================================================================

 DOI: 10.1101/2025.04.13.648639
 Dandiset: 000402
 Confidence: 10
 Reasoning: The citing paper explicitly uses the MICrONS dataset as a source of images to curate 'EM-5M', a
large-scale dataset specifically designed to train a foundation model (EM-DINO) for electron microscopy.

 DOI: 10.1101/2022.04.08.487573
 Dandiset: 000020
 Confidence: 10
 Reasoning: The authors explicitly state that they used the dataset (along with others) to train their
'NEUROeSTIMator' neural network. The data was partitioned into training, testing, and cross-validation sets
specifically to teach the model to predict activity-dependent gene expression through a bottleneck layer.

================================================================================
 TEACHING
================================================================================

 DOI: 10.21105/jose.00309
 Dandiset: 000582
 Confidence: 10
 Reasoning: The citing paper describes 'nwb4edu', which is explicitly an online textbook and educational resource
designed to teach students data analysis using DANDI datasets.

 DOI: 10.21105/jose.00118
 Dandiset: 000017
 Confidence: 10
 Reasoning: The citing paper describes Neuromatch Academy, an online summer school. The dataset (Steinmetz et al.
2019) is the primary resource used for student projects and tutorials to teach computational neuroscience analysis
techniques.