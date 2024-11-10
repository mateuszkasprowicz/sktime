# copyright: sktime developers, BSD-3-Clause License (see LICENSE file)
"""Implements a compositor that selects an ideal forecaster from a given transformer."""

__author__ = ["shlok191"]

from sktime.base._meta import _HeterogenousMetaEstimator
from sktime.forecasting.base import BaseForecaster
from sktime.registry import coerce_scitype


class TransformSelectForecaster(BaseForecaster, _HeterogenousMetaEstimator):
    """Compositor that utilizes varying forecasters by time series data's nature.

    Applies a series-to-primitives transformer on a given time series. Based on the
    generated value from the transformer, one of multiple forecasters provided by
    the user in the form of a dictionary (key => category, value => forecaster) is
    selected. Finally, the chosen forecaster is fit to the data for future predictions.

    Parameters
    ----------
    forecasters : dict[sktime forecasters]
        dict of forecasters with the key corresponding to categories generated
        by the given transformer and the value corresponding to a sktime forecaster.

    transformer : sktime transformer, default = ADICVTransformer()
        A series-to-primitives sk-time transformer that generates a value
        which can be used to quantify a choice of forecaster for the time series.

        Note: To ensure correct functionality, the transformer must store the
        generated category in the first column of the returned values when
        the transform() or fit_transform() functions are called.

    fallback_forecaster : sktime forecaster | None, Optional
        A fallback forecaster that will be used if the category generated by
        the transformer does not match any of the given forecasters.

    pooling : str, optional, default = "local", one of {"local", "global"}

    Raises
    ------
    AssertionError: If a valid transformer (an instance of BaseTransformer)
    is not passed or if valid forecasters (instances of BaseForecaster) are not given.

    Examples
    --------
    This example showcases how the TransformSelectForecaster can be utilized to select
    appropriate forecasters on the basis of the time series category determined by
    the ADICVTransformer!

    >>> from sktime.forecasting.compose._transform_select_forecaster import (
    ...    TransformSelectForecaster)
    >>> from sktime.forecasting.croston import Croston
    >>> from sktime.forecasting.trend import PolynomialTrendForecaster
    >>> from sktime.forecasting.naive import NaiveForecaster
    >>> from sktime.transformations.series.adi_cv import ADICVTransformer

    # Importing the methods which can generate data of specific categories
    depending on their variance and average demand intervals.

    >>> from sktime.transformations.series.tests.test_adi_cv import (
    ...     _generate_erratic_series)

    # The forecaster is defined which accepts a dictionary of forecasters,
    a transformer and optionally a fallback_forecaster

    >>> group_forecaster = TransformSelectForecaster(
    ...     forecasters =
    ...         {"smooth": NaiveForecaster(),
    ...         "erratic": Croston(),
    ...         "intermittent": PolynomialTrendForecaster()},
    ...     transformer=ADICVTransformer(features=["class"]))

    >>> generated_data = _generate_erratic_series()

    # The fit function firstly passes the data through the given transformer
    # to generate a given category. This category can be seen by the variable
    # self.category_.

    >>> group_forecaster = group_forecaster.fit(generated_data, fh=50)
    >>> #print(f"The chosen category is: {group_forecaster.category}")

    >>> # Print out the predicted value over the given forecasting horizon!
    >>> # print(group_forecaster.predict(fh=50, X=None))
    """

    _tags = {
        "y_inner_mtype": "pd.DataFrame",
        "X_inner_mtype": "pd.DataFrame",
        "scitype:y": "both",
        "ignores-exogeneous-X": False,
        "requires-fh-in-fit": False,
        "enforce_index_type": None,
        "authors": ["shlok191"],
        "maintainers": ["shlok191"],
        "python_version": None,
    }

    _steps_attr = "_forecasters"

    def __init__(
        self,
        forecasters,
        transformer=None,
        fallback_forecaster=None,
    ):
        self.transformer = transformer
        self.forecasters = forecasters
        self.fallback_forecaster = fallback_forecaster

        super().__init__()

        # saving arguments to object storage
        if transformer is not None:
            self.transformer = transformer
        else:
            from sktime.transformations.series.adi_cv import ADICVTransformer

            self.transformer = ADICVTransformer(features=["class"])

        self.transformer_ = coerce_scitype(self.transformer, "transformer").clone()

        for forecaster in forecasters.values():
            assert isinstance(forecaster, BaseForecaster)

        self.forecasters_ = {k: f.clone() for k, f in forecasters.items()}

        # All checks OK!

        # Assigning all capabilities on the basis of the capabilities
        # of the passed forecasters
        true_if_all_tags = {
            "ignores-exogeneous-X": True,
            "X-y-must-have-same-index": True,
            "enforce_index_type": True,
            "handles-missing-data": True,
            "capability:insample": True,
            "capability:pred_int": True,
            "capability:pred_int:insample": True,
        }

        # Extrapolating values for flags that should be True if they are
        # True for all of the given forecasters!
        for tag in true_if_all_tags.keys():
            # Checking the equivalent forecaster tags
            true_for_all = True

            for forecaster in self.forecasters.values():
                # Fetching the forecaster tags
                forecaster_tags = forecaster.get_tags()

                if tag not in forecaster_tags or forecaster_tags[tag] is False:
                    true_for_all = False
                    break

            # Perform this check for the fallback forecaster too
            if fallback_forecaster is not None and (
                tag not in fallback_forecaster.get_tags()
                or fallback_forecaster.get_tags()[tag] is False
            ):
                true_for_all = False

            true_if_all_tags[tag] = true_for_all

        # Extrapolating values for flags that should be True if they are
        # True for any of the given forecasters!
        true_if_any_tags = {
            "requires-fh-in-fit": True,
            "X-y-must-have-same-index": True,
        }

        # Create a list of forecasters
        forecasters = list(self.forecasters.items())

        if self.fallback_forecaster is not None:
            forecasters.append(("", self.fallback_forecaster))

        # Update the tags by iterating through all tags
        for tag in true_if_any_tags:
            self._anytagis_then_set(tag, True, False, forecasters)

        # Update the tags
        self.set_tags(**true_if_all_tags)

        # Finally, dynamically adding implementation of probabilistic
        # functions depending on the tags set.
        if self.get_tags()["capability:pred_int"]:
            self._predict_interval = _predict_interval
            self._predict_var = _predict_var
            self._predict_proba = _predict_proba

    @property
    def _steps(self):
        return [self._coerce_estimator_tuple(self.transformer)] + self._forecasters

    @property
    def steps_(self):
        return [self._coerce_estimator_tuple(self.transformer_)] + self._forecasters

    def _fit(self, y, X=None, fh=None):
        """Fit forecaster to training data.

        private _fit containing the core logic, called from fit

        For the _fit function to work as intended, the transformer
        must generate and store the extrapolated category in the
        first column.

        Writes to self:
            Sets fitted model attributes ending in "_".

        Parameters
        ----------
        y : Pd.Series
            The target time series to which we fit the data.

        fh : ForecastingHorizon | None, optional (default=None)
            The forecasting horizon with the steps ahead to predict.

        X : Pd.Series | None, optional (default=None)
            No exogenous variables are used for this.

        Returns
        -------
        self : reference to self

        Raises
        ------
        ValueError: If the extrapolated category has no provided forecaster
        and if there is no fallback forecaster provided to the object!

        Example:

        If the passed transformer is an ADICVTransformer(), and the generated
        series is a lumpy series; however, if there is no key matching "lumpy"
        in the forecasters parameter, the fallback_forecaster will be used.
        Additionally, if the fallback_forecaster is None, a ValueError will be thrown.
        """
        # passing time series through the provided transformer!

        self.category_ = self.transformer_.fit_transform(X=y, y=X).iloc[0, 0]

        # check if we have an available forecaster
        if self.category_ not in self.forecasters:
            if self.fallback_forecaster is None:
                raise ValueError(
                    "Forecaster not provided for given"
                    + f"time series of type {self.category_}"
                    + "and no fallback forecaster provided to use for this case."
                )

            # Adopt the fallback forecaster if possible
            else:
                self.chosen_forecaster_ = self.fallback_forecaster.clone()

        else:
            self.chosen_forecaster_ = self.forecasters_[self.category_].clone()

        # fitting the forecaster!
        self.chosen_forecaster_.fit(y=y, X=X, fh=fh)

        return self

    def _predict(self, fh, X):
        """Forecast time series at future horizon.

        private _predict containing the core logic, called from predict

        State required:
            Requires state to be "fitted".

        Accesses in self:
            Fitted model attributes ending in "_"
            self.cutoff

        Parameters
        ----------
        fh : guaranteed to be ForecastingHorizon or None, optional (default=None)
            The forecasting horizon with the steps ahead to to predict.
            If not passed in _fit, guaranteed to be passed here

        X : sktime time series object, optional (default=None)
            guaranteed to be of an mtype in self.get_tag("X_inner_mtype")
            Exogeneous time series for the forecast

        Returns
        -------
        y_pred : sktime time series object
            should be of the same type as seen in _fit, as in "y_inner_mtype" tag
            Point predictions
        """
        # Obtain the prediction values for the given horizon.
        y_pred = self.chosen_forecaster_.predict(fh=fh, X=X)

        return y_pred

    def _update(self, y, X=None, update_params=True):
        """Update time series to incremental training data.

        Does not update the extrapolated category from the given
        transformer, and thus the chosen forecaster remains the same.

        State required:
            Requires state to be "fitted".

        Accesses in self:
            Fitted model attributes ending in "_"
            self.cutoff

        Writes to self:
            Sets fitted model attributes ending in "_", if update_params=True.
            Does not write to self if update_params=False.

        Parameters
        ----------
        y : sktime time series object
            guaranteed to be of an mtype in self.get_tag("y_inner_mtype")
            Time series with which to update the forecaster.
            if self.get_tag("scitype:y")=="univariate":
                guaranteed to have a single column/variable
            if self.get_tag("scitype:y")=="multivariate":
                guaranteed to have 2 or more columns
            if self.get_tag("scitype:y")=="both": no restrictions apply
        X :  sktime time series object, optional (default=None)
            guaranteed to be of an mtype in self.get_tag("X_inner_mtype")
            Exogeneous time series for the forecast
        update_params : bool, optional (default=True)
            whether model parameters should be updated

        Returns
        -------
        self : reference to self
        """
        self.chosen_forecaster_.update(y=y, X=X, update_params=update_params)

    @classmethod
    def get_test_params(cls, parameter_set="default"):
        """Return testing parameter settings for the estimator.

        Parameters
        ----------
        parameter_set : str, default="default"
            Name of the set of test parameters to return, for use in tests. If no
            special parameters are defined for a value, will return `"default"` set.
            There are currently no reserved values for forecasters.

        Returns
        -------
        params : dict or list of dict, default = {}
            Parameters to create testing instances of the class
            Each dict are parameters to construct an "interesting" test instance, i.e.,
            `MyClass(**params)` or `MyClass(**params[i])` creates a valid test instance.
            `create_test_instance` uses the first (or only) dictionary in `params`
        """
        from sktime.clustering.dbscan import TimeSeriesDBSCAN
        from sktime.forecasting.croston import Croston
        from sktime.forecasting.naive import NaiveForecaster
        from sktime.forecasting.trend import PolynomialTrendForecaster
        from sktime.transformations.series.adi_cv import ADICVTransformer

        param1 = {
            "forecasters": {
                "smooth": NaiveForecaster(),
                "erratic": PolynomialTrendForecaster(),
                "intermittent": Croston(),
                "lumpy": NaiveForecaster(),
            },
            "transformer": ADICVTransformer(features=["class"]),
            "fallback_forecaster": None,
        }

        # Attempting to utilize the fallback forecaster
        param2 = {
            "forecasters": {},
            "transformer": ADICVTransformer(features=["class"]),
            "fallback_forecaster": Croston(),
        }

        # use with clusterer
        param3 = {
            "forecasters": {},
            "transformer": TimeSeriesDBSCAN.create_test_instance(),
            "fallback_forecaster": Croston(),
        }

        params = [param1, param2, param3]
        return params

    @property
    def _forecasters(self):
        """Provides an internal list of the forecasters available.

        Each list item is a tuple of the format (category, forecaster)
        where the category for which the respective forecaster is chosen
        and the forecaster itself as the values for each tuple.

        Returns
        -------
        forecasters : list[tuple[str, strsktime forecasters]]
            The list of forecasters which is returned. Also includes the
            fallback forecaster with the category: "fallback_forecaster"
        """
        return list(self.forecasters.items()) + [
            ("fallback_forecaster", self.fallback_forecaster)
        ]

    @_forecasters.setter
    def _forecasters(self, new_forecasters):
        """Provide new values for the forecasters.

        Parameters
        ----------
        new_forecasters : list[tuple[str, strsktime forecasters]]
            The list of new forecasters to update the object's forecasters with
        """
        # Accepting in possible new forecasters
        for category, forecaster in new_forecasters:
            # We assign this in a different way
            if category != "fallback_forecaster":
                self.forecasters[category] = forecaster

            else:
                self.fallback_forecaster = forecaster


# Function implementations that will be added dynamically
# if the conditions are met. explained further above!
def _predict_interval(self, fh, X, coverage):
    """Compute/return prediction quantiles for a forecast.

    private _predict_interval containing the core logic,
        called from predict_interval and possibly predict_quantiles

    State required:
        Requires state to be "fitted".

    Accesses in self:
        Fitted model attributes ending in "_"
        self.cutoff

    Parameters
    ----------
    fh : guaranteed to be ForecastingHorizon
        The forecasting horizon with the steps ahead to to predict.
    X :  sktime time series object, optional (default=None)
        guaranteed to be of an mtype in self.get_tag("X_inner_mtype")
        Exogeneous time series for the forecast
    coverage : list of float (guaranteed not None and floats in [0,1] interval)
        nominal coverage(s) of predictive interval(s)

    Returns
    -------
    pred_int : pd.DataFrame
        Column has multi-index: first level is variable name from y in fit,
            second level coverage fractions for which intervals were computed.
                in the same order as in input `coverage`.
            Third level is string "lower" or "upper", for lower/upper interval end.
        Row index is fh, with additional (upper) levels equal to instance levels,
            from y seen in fit, if y_inner_mtype is Panel or Hierarchical.
        Entries are forecasts of lower/upper interval end,
            for var in col index, at nominal coverage in second col index,
            lower/upper depending on third col index, for the row index.
            Upper/lower interval end forecasts are equivalent to
            quantile forecasts at alpha = 0.5 - c/2, 0.5 + c/2 for c in coverage.
    """
    # Call this function for the chosen forecaster
    return self.chosen_forecaster_.predict_interval(fh=fh, X=X, coverage=coverage)


def _predict_var(self, fh, X=None, cov=False):
    """Forecast variance at future horizon.

    private _predict_var containing the core logic, called from predict_var

    Parameters
    ----------
    fh : guaranteed to be ForecastingHorizon or None, optional (default=None)
        The forecasting horizon with the steps ahead to to predict.
        If not passed in _fit, guaranteed to be passed here
    X :  sktime time series object, optional (default=None)
        guaranteed to be of an mtype in self.get_tag("X_inner_mtype")
        Exogeneous time series for the forecast
    cov : bool, optional (default=False)
        if True, computes covariance matrix forecast.
        if False, computes marginal variance forecasts.

    Returns
    -------
    pred_var : pd.DataFrame, format dependent on `cov` variable
        If cov=False:
            Column names are exactly those of `y` passed in `fit`/`update`.
                For nameless formats, column index will be a RangeIndex.
            Row index is fh, with additional levels equal to instance levels,
                from y seen in fit, if y_inner_mtype is Panel or Hierarchical.
            Entries are variance forecasts, for var in col index.
            A variance forecast for given variable and fh index is a predicted
                variance for that variable and index, given observed data.
        If cov=True:
            Column index is a multiindex: 1st level is variable names (as above)
                2nd level is fh.
            Row index is fh, with additional levels equal to instance levels,
                from y seen in fit, if y_inner_mtype is Panel or Hierarchical.
            Entries are (co-)variance forecasts, for var in col index, and
                covariance between time index in row and col.
            Note: no covariance forecasts are returned between different variables.
    """
    return self.chosen_forecaster_.predict_var(fh=fh, X=X, cov=cov)


def _predict_proba(self, fh, X, marginal=True):
    """Compute/return fully probabilistic forecasts.

    private _predict_proba containing the core logic, called from predict_proba

    Parameters
    ----------
    fh : int, list, np.array or ForecastingHorizon (not optional)
        The forecasting horizon encoding the time stamps to forecast at.
        if has not been passed in fit, must be passed, not optional
    X : sktime time series object, optional (default=None)
            Exogeneous time series for the forecast
        Should be of same scitype (Series, Panel, or Hierarchical) as y in fit
        if self.get_tag("X-y-must-have-same-index"),
            X.index must contain fh.index and y.index both
    marginal : bool, optional (default=True)
        whether returned distribution is marginal by time index

    Returns
    -------
    pred_dist : sktime BaseDistribution
        predictive distribution
        if marginal=True, will be marginal distribution by time point
        if marginal=False and implemented by method, will be joint
    """
    return self.chosen_forecaster_.predict_proba(fh=fh, X=X, marginal=marginal)
