from astropy import units as u
from astropy import log
import numpy as np
import warnings

from astropy.io.fits import Card

from . import wcs_utils
from . import cube_utils
from .utils import cached, WCSCelestialError

from radio_beam import Beam, Beams

__doctest_skip__ = ['SpatialCoordMixinClass.world']

DOPPLER_CONVENTIONS = {}
DOPPLER_CONVENTIONS['radio'] = u.doppler_radio
DOPPLER_CONVENTIONS['optical'] = u.doppler_optical
DOPPLER_CONVENTIONS['relativistic'] = u.doppler_relativistic


class BaseNDClass(object):

    _cache = {}

    @property
    def _nowcs_header(self):
        """
        Return a copy of the header with no WCS information attached
        """
        log.debug("Stripping WCS from header")
        return wcs_utils.strip_wcs_from_header(self._header)

    @property
    def wcs(self):
        return self._wcs

    @property
    def meta(self):
        return self._meta

    @property
    def mask(self):
        return self._mask

class HeaderMixinClass(object):
    """
    A mixin class to provide header updating from WCS objects.
    The parent object must have a WCS.
    """

    def wcs(self):
        raise TypeError("Classes inheriting from HeaderMixin must define a "
                        "wcs method")

    @property
    def header(self):
        header = self._nowcs_header

        wcsheader = self.wcs.to_header() if self.wcs is not None else {}

        # When preserving metadata, copy over keywords before doing the WCS
        # keyword copying, since those have specific formatting requirements
        # and will overwrite these in many cases (e.g., BMAJ)
        for key in self.meta:
            if key.upper() not in wcsheader:
                if isinstance(key, str) and len(key) <= 8:
                    try:
                        header[key.upper()] = str(self.meta[key])
                    except ValueError as ex:
                        # need a silenced-by-default warning here?
                        # log.warn("Skipped key {0} because {1}".format(key, ex))
                        pass
                elif isinstance(key, str) and len(key) > 8:
                    header['COMMENT'] = "{0}={1}".format(key, self.meta[key])

        # Preserve non-WCS information from previous header iteration
        header.update(wcsheader)
        if self.unit == u.dimensionless_unscaled and 'BUNIT' in self._meta:
            # preserve the BUNIT even though it's not technically valid
            # (Jy/Beam)
            header['BUNIT'] = self._meta['BUNIT']
        else:
            header['BUNIT'] = self.unit.to_string(format='FITS')

        if 'beam' in self._meta:
            header = self._meta['beam'].attach_to_header(header)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            header.insert(2, Card(keyword='NAXIS', value=self.ndim))
            for ind,sh in enumerate(self.shape[::-1]):
                header.insert(3+ind, Card(keyword='NAXIS{0:1d}'.format(ind+1),
                                          value=sh))

        return header


class SpatialCoordMixinClass(object):

    @property
    def _has_wcs_celestial(self):
        return self.wcs.has_celestial

    def _raise_wcs_no_celestial(self):
        if not self._has_wcs_celestial:
            raise WCSCelestialError("WCS does not contain two spatial axes.")

    @cube_utils.slice_syntax
    def world(self, view):
        """
        Return a list of the world coordinates in a cube, projection, or a view
        of it.

        SpatialCoordMixinClass.world is called with *bracket notation*, like
        a NumPy array::

            c.world[0:3, :, :]

        Returns
        -------
        [v, y, x] : list of NumPy arrays
            The 3 world coordinates at each pixel in the view. For a 2D image,
            the output is ``[y, x]``.


        Examples
        --------
        Extract the first 3 velocity channels of the cube:

        >>> v, y, x = c.world[0:3]

        Extract all the world coordinates:

        >>> v, y, x = c.world[:, :, :]

        Extract every other pixel along all axes:

        >>> v, y, x = c.world[::2, ::2, ::2]

        Extract all the world coordinates for a 2D image:

        >>> y, x = c.world[:, :]

        """

        self._raise_wcs_no_celestial()

        # note: view is a tuple of view

        # the next 3 lines are equivalent to (but more efficient than)
        # inds = np.indices(self._data.shape)
        # inds = [i[view] for i in inds]
        inds = np.ogrid[[slice(0, s) for s in self.shape]]
        inds = np.broadcast_arrays(*inds)
        inds = [i[view] for i in inds[::-1]]  # numpy -> wcs order

        shp = inds[0].shape
        inds = np.column_stack([i.ravel() for i in inds])
        world = self._wcs.all_pix2world(inds, 0).T

        world = [w.reshape(shp) for w in world]  # 1D->3D

        # apply units
        world = [w * u.Unit(self._wcs.wcs.cunit[i])
                 for i, w in enumerate(world)]

        # convert spectral unit if needed
        if hasattr(self, "_spectral_unit"):
            if self._spectral_unit is not None:
                specind = self.wcs.wcs.spec
                world[specind] = world[specind].to(self._spectral_unit)

        return world[::-1]  # reverse WCS -> numpy order

    def world_spines(self):
        """
        Returns a list of 1D arrays, for the world coordinates
        along each pixel axis.

        Raises error if this operation is ill-posed (e.g. rotated world
        coordinates, strong distortions)

        This method is not currently implemented. Use :meth:`world` instead.
        """
        raise NotImplementedError()

    @property
    def spatial_coordinate_map(self):
        view = [0 for ii in range(self.ndim - 2)] + [slice(None)] * 2
        return self.world[view][self.ndim - 2:]

    @property
    @cached
    def world_extrema(self):
        corners = [(0, self.shape[2]-1),
                   (self.shape[1]-1, 0),
                   (self.shape[1]-1, self.shape[2]-1),
                   (0,0)]
        latlon_corners = [self.world[0, y, x] for y,x in corners]
        lon = u.Quantity([x for z,y,x in latlon_corners])
        lat = u.Quantity([y for z,y,x in latlon_corners])

        _lon_min = lon.min()
        _lon_max = lon.max()
        _lat_min = lat.min()
        _lat_max = lat.max()

        return u.Quantity(((_lon_min.to(u.deg).value, _lon_max.to(u.deg).value),
                           (_lat_min.to(u.deg).value, _lat_max.to(u.deg).value)),
                          u.deg)

    @property
    @cached
    def longitude_extrema(self):
        return self.world_extrema[0]

    @property
    @cached
    def latitude_extrema(self):
        return self.world_extrema[1]


class SpectralAxisMixinClass(object):

    def _new_spectral_wcs(self, unit, velocity_convention=None,
                          rest_value=None):
        """
        Returns a new WCS with a different Spectral Axis unit

        Parameters
        ----------
        unit : :class:`~astropy.units.Unit`
            Any valid spectral unit: velocity, (wave)length, or frequency.
            Only vacuum units are supported.
        velocity_convention : 'relativistic', 'radio', or 'optical'
            The velocity convention to use for the output velocity axis.
            Required if the output type is velocity. This can be either one
            of the above strings, or an `astropy.units` equivalency.
        rest_value : :class:`~astropy.units.Quantity`
            A rest wavelength or frequency with appropriate units.  Required if
            output type is velocity.  The cube's WCS should include this
            already if the *input* type is velocity, but the WCS's rest
            wavelength/frequency can be overridden with this parameter.

            .. note: This must be the rest frequency/wavelength *in vacuum*,
                     even if your cube has air wavelength units

        """
        from .spectral_axis import (convert_spectral_axis,
                                    determine_ctype_from_vconv)

        # Allow string specification of units, for example
        if not isinstance(unit, u.Unit):
            unit = u.Unit(unit)

        # Velocity conventions: required for frq <-> velo
        # convert_spectral_axis will handle the case of no velocity
        # convention specified & one is required
        if velocity_convention in DOPPLER_CONVENTIONS:
            velocity_convention = DOPPLER_CONVENTIONS[velocity_convention]
        elif (velocity_convention is not None and
              velocity_convention not in DOPPLER_CONVENTIONS.values()):
            raise ValueError("Velocity convention must be radio, optical, "
                             "or relativistic.")

        # If rest value is specified, it must be a quantity
        if (rest_value is not None and
            (not hasattr(rest_value, 'unit') or
             not rest_value.unit.is_equivalent(u.m, u.spectral()))):
            raise ValueError("Rest value must be specified as an astropy "
                             "quantity with spectral equivalence.")

        # Shorter versions to keep lines under 80
        ctype_from_vconv = determine_ctype_from_vconv

        meta = self._meta.copy()
        if 'Original Unit' not in self._meta:
            meta['Original Unit'] = self._wcs.wcs.cunit[self._wcs.wcs.spec]
            meta['Original Type'] = self._wcs.wcs.ctype[self._wcs.wcs.spec]

        out_ctype = ctype_from_vconv(self._wcs.wcs.ctype[self._wcs.wcs.spec],
                                     unit,
                                     velocity_convention=velocity_convention)

        newwcs = convert_spectral_axis(self._wcs, unit, out_ctype,
                                       rest_value=rest_value)

        newwcs.wcs.set()
        return newwcs, meta

    @property
    def spectral_axis(self):
        # spectral objects should be forced to implement this
        raise NotImplementedError


class MaskableArrayMixinClass(object):
    """
    Mixin class for maskable arrays
    """

    def _get_filled_data(self, view=(), fill=np.nan, check_endian=False,
                         use_memmap=None):
        """
        Return the underlying data as a numpy array.
        Always returns the spectral axis as the 0th axis

        Sets masked values to *fill*
        """
        if check_endian:
            if not self._data.dtype.isnative:
                kind = str(self._data.dtype.kind)
                sz = str(self._data.dtype.itemsize)
                dt = '=' + kind + sz
                data = self._data.astype(dt)
            else:
                data = self._data
        else:
            data = self._data

        if self._mask is None:
            return data[view]

        if use_memmap is None and hasattr(self, '_is_huge'):
            use_memmap = self._is_huge

        return self._mask._filled(data=data, wcs=self._wcs, fill=fill,
                                  view=view, wcs_tolerance=self._wcs_tolerance,
                                  use_memmap=use_memmap
                                 )

    @cube_utils.slice_syntax
    def filled_data(self, view):
        """
        Return a portion of the data array, with excluded mask values
        replaced by :meth:`fill_value`.

        Returns
        -------
        data : Quantity
            The masked data.
        """
        return u.Quantity(self._get_filled_data(view, fill=self._fill_value),
                          self.unit, copy=False)

    def filled(self, fill_value=None):
        if fill_value is not None:
            return u.Quantity(self._get_filled_data(fill=fill_value),
                              self.unit, copy=False)
        return self.filled_data[:]

    @cube_utils.slice_syntax
    def unitless_filled_data(self, view):
        """
        Return a portion of the data array, with excluded mask values
        replaced by :meth:`fill_value`.

        Returns
        -------
        data : numpy.array
            The masked data.
        """
        return self._get_filled_data(view, fill=self._fill_value)

    @property
    def fill_value(self):
        """ The replacement value used by :meth:`filled_data`.

        fill_value is immutable; use :meth:`with_fill_value`
        to create a new cube with a different fill value.
        """
        return self._fill_value

class MultiBeamMixinClass(object):
    """
    A mixin class to handle multibeam objects.  To be used by
    VaryingResolutionSpectralCube's and OneDSpectrum's """

    def jtok_factors(self, equivalencies=()):
        """
        Compute an array of multiplicative factors that will convert from
        Jy/beam to K
        """

        factors = []
        for bm,frq in zip(self.beams,
                          self.with_spectral_unit(u.Hz).spectral_axis):

            # create a beam equivalency for brightness temperature
            bmequiv = bm.jtok_equiv(frq)
            factor = (u.Jy).to(u.K, equivalencies=bmequiv+list(equivalencies))
            factors.append(factor)
        factor = np.array(factors)

        return factor

    @property
    def beams(self):
        return self._beams

    @beams.setter
    def beams(self, obj):

        if not isinstance(obj, Beams):
            raise TypeError("beam must be a radio_beam.Beams object.")

        if not obj.size == self.shape[0]:
            raise ValueError("The Beams object must have the same size as the "
                             "data. Found a size of {0} and the data have a "
                             "size of {1}".format(obj.size, self.size))

        self._beams = obj


class BeamMixinClass(object):
    """
    Functionality for objects with a single beam.

    Specific objects (cubes, LDOs) still need to define their own `with_beam`
    methods.
    """

    @property
    def beam(self):
        return self._beam

    @beam.setter
    def beam(self, obj):

        if not isinstance(obj, Beam):
            raise TypeError("beam must be a radio_beam.Beam object.")

        self._beam = obj
