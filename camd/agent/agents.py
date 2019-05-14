# Copyright Toyota Research Institute 2019

import numpy as np
from qmpy.analysis.thermodynamics.phase import Phase, PhaseData
from copy import deepcopy
from camd.analysis import PhaseSpaceAL, ELEMENTS
from camd.agent.base import HypothesisAgent, QBC

# TODO: Adaptive N_query and subsampling of candidate space


class QBCStabilityAgent(HypothesisAgent):

    def __init__(self, candidate_data, seed_data, N_query=None,
                 pd=None, hull_distance=None, ML_algorithm=None, ML_algorithm_params=None,
                 N_members=None, frac=None, multiprocessing=True):

        self.candidate_data = candidate_data
        self.seed_data = seed_data
        self.hull_distance = hull_distance if hull_distance else 0.0
        self.N_query = N_query if N_query else 1
        self.pd = pd
        self.ML_algorithm = ML_algorithm
        self.ML_algorithm_params = ML_algorithm_params
        self.N_members = N_members if N_members else 10
        self.frac = frac if frac else 0.5
        self.multiprocessing = multiprocessing

        self.cv_score = np.nan

        self.qbc = QBC(N_members=self.N_members, frac=self.frac,
                       ML_algorithm=self.ML_algorithm, ML_algorithm_params=self.ML_algorithm_params)

        super(QBCStabilityAgent, self).__init__()

    def get_hypotheses(self, retrain_committee=False):
        if retrain_committee:
            self.qbc.trained = False

        if not self.qbc.trained:
            self.qbc.fit(self.seed_data, self.seed_data['delta_e'], ignore_columns=['Composition', 'N_species', 'delta_e'])
        self.cv_score = self.qbc.cv_score

        # QBC makes predictions for Hf and uncertainty on candidate data
        preds, stds = self.qbc.predict(self.candidate_data)
        expected = preds - stds

        # This is just curbing outrageously negative predictions
        for i in range(len(expected)):
            if expected[i] < -6.0:
                expected[i] = -6.0

        # Get estimated stabilities from ML predictions
        # For that, let's create Phases from candidates
        candidate_phases = []
        _c = 0
        for data in self.candidate_data.iterrows():
            candidate_phases.append(
                Phase(data[1]['Composition'], energy=expected[_c], per_atom=True, description=data[0]))
            _c += 1

        # We take the existing phase data for seed phases, add candidate phases, and compute stabilities
        if not self.pd:
            self.get_pd()

        pd_ml = deepcopy(self.pd)
        pd_ml.add_phases(candidate_phases)
        space_ml = PhaseSpaceAL(bounds=ELEMENTS, data=pd_ml)
        if self.multiprocessing:
            space_ml.compute_stabilities_multi(candidate_phases)
        else:
            space_ml.compute_stabilities_mod(candidate_phases)

        ml_stabilities = []
        for _p in candidate_phases:
            ml_stabilities.append(_p.stability)

        # Now let's find the most stable ones upto N_query within hull_distance
        ml_stabilities = np.array(ml_stabilities, dtype=float)
        ml_stable = np.array(candidate_phases)[ml_stabilities <= self.hull_distance]
        to_compute = sorted(ml_stable, key=lambda x: x.stability)[:self.N_query]

        self.indices_to_compute = [i.description for i in to_compute]

        return self.indices_to_compute

    def get_pd(self):
        self.pd = PhaseData()
        phases = []
        for data in self.seed_data.iterrows():
            phases.append(Phase(data[1]['Composition'], energy=data[1]['delta_e'], per_atom=True, description=data[0]))
        for el in ELEMENTS:
            phases.append(Phase(el, 0.0, per_atom=True))
        self.pd.add_phases(phases)


class AgentStabilityML5(HypothesisAgent):
    """
    An agent that does a certain fraction of full exploration an exploitation in each iteration.
    It will exploit a fraction of N_query options (frac), and explore the rest of its budget.
    """
    def __init__(self, candidate_data, seed_data, N_query=None,
                 pd=None, hull_distance=None, ML_algorithm=None, ML_algorithm_params=None,
                 frac=None, multiprocessing=True):

        self.candidate_data = candidate_data
        self.seed_data = seed_data
        self.hull_distance = hull_distance if hull_distance else 0.0
        self.N_query = N_query if N_query else 1
        self.pd = pd
        self.ML_algorithm = ML_algorithm
        self.ML_algorithm_params = ML_algorithm_params
        self.multiprocessing = multiprocessing
        self.frac = frac if frac else 0.5
        self.cv_score = np.nan

        super(AgentStabilityML5, self).__init__()

    def get_hypotheses(self):

        overall_model = self.ML_algorithm(**self.ML_algorithm_params)
        X = self.seed_data.drop(['Composition', 'delta_e', 'N_species'], axis=1)

        from sklearn.preprocessing import StandardScaler
        overall_scaler = StandardScaler()
        X = overall_scaler.fit_transform(X, self.seed_data['delta_e'])
        overall_model.fit(X, self.seed_data['delta_e'])

        from sklearn.model_selection import cross_val_score, KFold
        cv_score = cross_val_score(overall_model, X, self.seed_data['delta_e'],
                                   cv=KFold(5, shuffle=True), scoring='neg_mean_absolute_error')
        self.cv_score = np.mean(cv_score)*-1

        cand_X = self.candidate_data.drop(['Composition', 'delta_e', 'N_species'], axis=1)
        cand_X = overall_scaler.transform(cand_X)
        expected = overall_model.predict(cand_X)

        # this is just curbing outrageously negative predictions
        for i in range(len(expected)):
            if expected[i] < -6.0:
                expected[i] = -6.0

        # Get estimated stabilities from ML predictions
        # For that, let's create Phases from candidates
        candidate_phases = []
        _c = 0
        for data in self.candidate_data.iterrows():
            candidate_phases.append(
                Phase(data[1]['Composition'], energy=expected[_c], per_atom=True, description=data[0]))
            _c += 1

        # We take the existing phase data for seed phases, add candidate phases, and compute stabilities
        if not self.pd:
            self.get_pd()

        pd_ml = deepcopy(self.pd)
        pd_ml.add_phases(candidate_phases)
        space_ml = PhaseSpaceAL(bounds=ELEMENTS, data=pd_ml)
        if self.multiprocessing:
            space_ml.compute_stabilities_multi(candidate_phases)
        else:
            space_ml.compute_stabilities_mod(candidate_phases)

        ml_stabilities = []
        for _p in candidate_phases:
            ml_stabilities.append(_p.stability)

        # Now let's find the most stable ones upto N_query within hull_distance
        ml_stabilities = np.array(ml_stabilities, dtype=float)
        ml_stable = np.array(candidate_phases)[ml_stabilities <= self.hull_distance]

        sorted_stabilities = sorted(ml_stable, key=lambda x: x.stability)

        # Exploitation part:
        to_compute = sorted_stabilities[:int(self.N_query * self.frac)]
        remaining = sorted_stabilities[int(self.N_query * self.frac):]

        # Exploration part:
        np.random.shuffle(remaining)
        to_compute += remaining[:int(self.N_query * (1.0-self.frac))]

        self.indices_to_compute = [i.description for i in to_compute]
        return self.indices_to_compute

    def get_pd(self):
        self.pd = PhaseData()
        phases = []
        for data in self.seed_data.iterrows():
            phases.append(Phase(data[1]['Composition'], energy=data[1]['delta_e'], per_atom=True, description=data[0]))
        for el in ELEMENTS:
            phases.append(Phase(el, 0.0, per_atom=True))
        self.pd.add_phases(phases)